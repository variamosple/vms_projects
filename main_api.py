import os
import re
import logging
from fastapi import FastAPI, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from uvicorn.logging import DefaultFormatter

from variamos_security import (load_keys, is_authenticated, has_roles, has_permissions, SessionUser, VariamosSecurityException, variamos_security_exception_handler)

import uuid
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import httpx

from src.db_connector import get_db, SessionLocal
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from src.model.modelDAO import UserDao, ProjectDao
from pydantic import BaseModel
from src.infrastructure.entry_points import (
    projects_admin_controller_v1,
    models_admin_controller_v1
)

# Configure logging
formatter = DefaultFormatter()
handler = logging.StreamHandler()
handler.setFormatter(formatter)

logging.basicConfig(level=logging.DEBUG, handlers=[handler])
logger = logging.getLogger(__name__)

class ShareProjectInput(BaseModel):
    user_email: str
    project_id: str
    user_role: str

class ConfigurationInput(BaseModel):
    project_json: dict
    id_feature_model: str
    config_name: str
    id: str


class ConfigurationInput2(BaseModel):
    id_feature_model: str
    id: str

class ChangeCollaboratorInput(BaseModel):
    project_id: str

class ChangeUserRoleInput(BaseModel):
    project_id: str
    collaborator_id: str
    role: str

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

class OpenRouterChatRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False

    class Config:
        extra = "allow"  # permite pasar campos extra si luego los necesitas (top_p, etc.)

_openrouter_keys: List[str] = []
_openrouter_key_state: Dict[str, Dict[str, Any]] = {}  # {key: {"cooldown_until": float, "disabled_until": float}}
_openrouter_last_idx: int = 0
_openrouter_client: Optional[httpx.AsyncClient] = None

def _load_openrouter_keys() -> List[str]:
    raw = (os.getenv("VARIAMOS_OPENROUTER_API_KEYS")
           or os.getenv("OPENROUTER_API_KEYS")
           or "").strip()

    if raw:
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        return keys

    single = (os.getenv("VARIAMOS_OPENROUTER_API_KEY")
              or os.getenv("OPENROUTER_API_KEY")
              or "").strip()
    return [single] if single else []

def _key_available_now(api_key: str, now: float) -> bool:
    st = _openrouter_key_state.get(api_key, {})
    cooldown_until = float(st.get("cooldown_until", 0.0) or 0.0)
    disabled_until = float(st.get("disabled_until", 0.0) or 0.0)
    return now >= cooldown_until and now >= disabled_until

def _mark_cooldown(api_key: str, seconds: float):
    now = time.time()
    st = _openrouter_key_state.setdefault(api_key, {})
    st["cooldown_until"] = max(float(st.get("cooldown_until", 0.0) or 0.0), now + max(0.0, seconds))

def _mark_disabled(api_key: str, seconds: float):
    now = time.time()
    st = _openrouter_key_state.setdefault(api_key, {})
    st["disabled_until"] = max(float(st.get("disabled_until", 0.0) or 0.0), now + max(0.0, seconds))

def _retry_after_seconds(resp: httpx.Response) -> float:
    ra = resp.headers.get("retry-after")
    if not ra:
        return 0.0
    try:
        return max(0.0, float(ra))
    except Exception:
        return 0.0

def _build_openrouter_headers(request: Request, api_key: str) -> Dict[str, str]:
    # Estos headers son recomendados por OpenRouter; no son “seguridad”, solo metadatos.
    # Mejor fijarlos desde server y no confiar en el cliente.
    referer = (request.headers.get("origin") or "https://app.variamos.com")
    title = "VariaMos"

    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": referer,
        "X-Title": title,
    }

async def _openrouter_post_with_key(payload: Dict[str, Any], request: Request, api_key: str) -> httpx.Response:
    global _openrouter_client
    if _openrouter_client is None:
        raise HTTPException(status_code=500, detail="OpenRouter client not initialized")

    headers = _build_openrouter_headers(request, api_key)
    return await _openrouter_client.post(OPENROUTER_URL, json=payload, headers=headers)

async def call_openrouter_rotate_keys(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    global _openrouter_last_idx

    if not _openrouter_keys:
        raise HTTPException(status_code=500, detail="No OpenRouter API keys configured")

    now = time.time()
    n = len(_openrouter_keys)

    # orden round-robin a partir de la última usada
    start = _openrouter_last_idx % n
    ordered = _openrouter_keys[start:] + _openrouter_keys[:start]

    last_err_msg = "All keys failed"

    for idx, api_key in enumerate(ordered):
        if not _key_available_now(api_key, now):
            continue

        try:
            resp = await _openrouter_post_with_key(payload, request, api_key)

            # 429: cooldown y probar otra key
            if resp.status_code == 429:
                ra = _retry_after_seconds(resp)
                _mark_cooldown(api_key, ra if ra > 0 else 2.0)
                last_err_msg = f"429 from OpenRouter (cooldown={ra}s)"
                continue

            # 5xx: cooldown corto y probar otra key
            if 500 <= resp.status_code <= 599:
                _mark_cooldown(api_key, 2.0)
                last_err_msg = f"{resp.status_code} from OpenRouter"
                continue

            # 401/403: key inválida/bloqueada → deshabilitar por más tiempo
            if resp.status_code in (401, 403):
                _mark_disabled(api_key, 3600.0)
                last_err_msg = f"{resp.status_code} from OpenRouter (disabled key 1h)"
                continue

            # Otros 4xx: generalmente es error de payload/model → no tiene sentido rotar keys
            if 400 <= resp.status_code <= 499:
                try:
                    data = resp.json()
                except Exception:
                    data = {"error": {"message": resp.text}}
                raise HTTPException(status_code=resp.status_code, detail=data)

            # OK
            _openrouter_last_idx = (start + idx + 1) % n
            return resp.json()

        except httpx.TimeoutException:
            _mark_cooldown(api_key, 2.0)
            last_err_msg = "timeout calling OpenRouter"
            continue
        except httpx.RequestError:
            _mark_cooldown(api_key, 2.0)
            last_err_msg = "network error calling OpenRouter"
            continue

    raise HTTPException(status_code=503, detail={"error": {"message": last_err_msg}})


app = FastAPI()

raw_patterns = [p.strip() for p in os.getenv("VARIAMOS_CORS_ALLOWED_ORIGINS_PATTERNS", "").split(",") if p.strip()]
ALLOWED_ORIGINS_PATTERNS = []
for p in raw_patterns:
    try:
        ALLOWED_ORIGINS_PATTERNS.append(re.compile(p))
    except re.error as e:
        logger.error(f"Invalid CORS origin regex: {p} -> {e}")


class CustomCORSMiddleware(CORSMiddleware):
    def is_allowed_origin(self, origin: str) -> bool:
        if not origin or origin == "null":
            return False
        if not ALLOWED_ORIGINS_PATTERNS:
            return False
        return any(p.match(origin) for p in ALLOWED_ORIGINS_PATTERNS)


app.add_middleware(
    CustomCORSMiddleware,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app.add_exception_handler(VariamosSecurityException, variamos_security_exception_handler)
app.include_router(projects_admin_controller_v1)
app.include_router(models_admin_controller_v1)

def enforce_allowed_web_origin(request: Request):
    if not ALLOWED_ORIGINS_PATTERNS:
        raise HTTPException(status_code=403, detail="CORS origins not configured")

    origin = (request.headers.get("origin") or "").strip()
    referer = (request.headers.get("referer") or "").strip()

    candidate = ""
    if origin:
        candidate = origin
    elif referer:
        try:
            u = urlparse(referer)
            if u.scheme and u.netloc:
                candidate = f"{u.scheme}://{u.netloc}"
        except Exception:
            candidate = ""

    if not candidate or candidate == "null":
        raise HTTPException(status_code=403, detail="Forbidden origin")

    if not any(p.match(candidate) for p in ALLOWED_ORIGINS_PATTERNS):
        raise HTTPException(status_code=403, detail="Forbidden origin")


@app.get("/version")
async def getVersion():
    return {"transactionId": "1", "message": "vms_projects 1.25.3.20.21"}

@app.get("/testdb")
async def testDb():
    return project_DAO.get_template_projects()

@app.on_event("startup")
async def iniciar_app():
    print("Se está inicializando la conexión con la base de datos")
    db = SessionLocal()
    global user_DAO
    global project_DAO
    user_DAO = UserDao(db)
    project_DAO = ProjectDao(db)
    load_keys()
    global _openrouter_keys, _openrouter_client
    try:
        _openrouter_keys = _load_openrouter_keys()
        if not _openrouter_keys:
            logger.warning("No OpenRouter keys configured (proxy will fail).")

        _openrouter_client = httpx.AsyncClient(timeout=25.0)
        logger.info("OpenRouter client initialized.")
    except Exception as e:
        _openrouter_client = None
        _openrouter_keys = []
        logger.exception(f"OpenRouter init failed (service will still run): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    global _openrouter_client
    try:
        if _openrouter_client is not None:
            await _openrouter_client.aclose()
    finally:
        close_db()



def close_db():
    db = SessionLocal()  # Aquí obtienes la sesión
    db.close()


@app.get("/getUser", dependencies=[Depends(is_authenticated)])
async def obtener_usuario(request: Request):
    user = request.state.user
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"data": {"user": user}}

@app.post("/saveProject", dependencies=[Depends(is_authenticated)])
async def guardar_modelo(request: Request, project_dict: dict):
    template=False
    user_id = request.state.user.id
    print("intento guardar modelo")
    project_id=project_dict['id']
    if project_id == None:
        print("project id is none")
        return project_DAO.create_project(project_dict, user_id)
    else:
        print("project is updated")
        return project_DAO.update_project(project_dict, user_id)


@app.get("/getProjects", dependencies=[Depends(is_authenticated)])
async def obtener_modelos(request: Request):
    user_id = request.state.user.id

    all_projects = user_DAO.get_projects(user_id)["data"]["projects"]
    
    owned_proyects = [project for project in all_projects if project["role"] == "owner"]
    shared_proyects = [
        project for project in all_projects
          if project["role"] != "owner"
          and project["is_collaborative"] == True
    ]

    return {
        "owned_projects": owned_proyects,
        "shared_projects": shared_proyects
    }

@app.get("/getTemplateProjects", dependencies=[Depends(is_authenticated)])
async def obtener_modelos_template():
    return project_DAO.get_template_projects()

@app.get("/getProject", dependencies=[Depends(is_authenticated)])
async def obtener_modelo(request: Request, project_id: str):
    user_id = request.state.user.id
    return project_DAO.get_by_id(user_id, project_id)


@app.post("/shareProject", dependencies=[Depends(is_authenticated)])
async def compartir_modelo(data: ShareProjectInput):

    user = user_DAO.get_by_email(data.user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return project_DAO.share_project(data.project_id, user.id, data.user_role)

@app.get("/usersProject", dependencies=[Depends(is_authenticated)])
async def obtener_usuarios_proyecto(request: Request, project_id: str):
    user_id = request.state.user.id
    return project_DAO.get_users(project_id, user_id)

@app.delete("/removeCollaborator", dependencies=[Depends(is_authenticated)])
async def delete_collaborator_endpoint(request: Request, project_id: str, collaborator_id: str):
    user_id = request.state.user.id
    return project_DAO.delete_collaborator(project_id, user_id, collaborator_id)

@app.post("/changeUserRole", dependencies=[Depends(is_authenticated)])
async def cambiar_rol_usuario(request: Request, data: ChangeUserRoleInput):
    user_id = request.state.user.id
    return project_DAO.change_user_role(data.project_id, user_id, data.collaborator_id, data.role)

@app.post("/changeProjectCollaborative", dependencies=[Depends(is_authenticated)])
async def cambiar_colaborativo(request: Request, data: ChangeCollaboratorInput):
    user_id = request.state.user.id
    return project_DAO.change_project_collaborative(data.project_id, user_id)

@app.get("/getUserRole", dependencies=[Depends(is_authenticated)])
async def obtener_rol_usuario(request: Request, project_id: str):
    user_id = request.state.user.id
    return project_DAO.get_user_role(project_id, user_id)

@app.get("/findUser")
async def buscar_usuario_email(user_mail: str, db: Session = Depends(get_db)):
    return user_DAO.get_by_email(user_mail)


@app.get("/permissionProject")
async def obtener_permisos(project_id: str, db: Session = Depends(get_db)):
    return None

@app.put("/updateProjectName", dependencies=[Depends(is_authenticated)])
async def update_project_name_endpoint(project_dict: dict):
    return project_DAO.update_project_name(project_dict)

@app.delete("/deleteProject", dependencies=[Depends(is_authenticated)])
async def delete_project_endpoint(project_dict: dict):
    return project_DAO.delete_project(project_dict)

@app.post("/addConfiguration", dependencies=[Depends(is_authenticated)])
def add_configuration(project_id: str, config_input: ConfigurationInput):
    try:
        return project_DAO.add_configuration(project_id, config_input.project_json, config_input.id_feature_model, config_input.config_name)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/deleteConfiguration", dependencies=[Depends(is_authenticated)])
def delete_configuration(project_id: str, model_id : str, configuration_id: str):
    try:
        return project_DAO.delete_configuration_from_project(project_id, model_id, configuration_id)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/getConfiguration", dependencies=[Depends(is_authenticated)])
def get_configuration(project_id: str, configuration_id: str):
    return project_DAO.get_configuration(project_id, configuration_id)

@app.get("/getAllConfigurations", dependencies=[Depends(is_authenticated)])
def get_model_configurations(project_id: str, model_id: str):
    return project_DAO.get_model_configurations(project_id, model_id)

@app.post("/applyConfiguration2", dependencies=[Depends(is_authenticated)])
def apply_configuration2(project_id : str, model_id : str, configuration_id: str):
    return project_DAO.apply_configuration(project_id, model_id, configuration_id)

@app.post("/applyConfiguration", dependencies=[Depends(is_authenticated)])
def apply_configuration(project_id : str, config_input: ConfigurationInput2):
    return project_DAO.apply_configuration(project_id, config_input.id_feature_model, config_input.id)

@app.post(
    "/api/ai/openrouter/chat",
    dependencies=[
        Depends(enforce_allowed_web_origin)
    ],
)
async def openrouter_chat_proxy(request: Request, body: OpenRouterChatRequest):
    payload = body.dict(exclude_none=True)

    # Validación mínima defensiva
    if not payload.get("model"):
        raise HTTPException(status_code=400, detail="Missing model")
    if not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise HTTPException(status_code=400, detail="Missing messages")

    data = await call_openrouter_rotate_keys(payload, request)
    return data

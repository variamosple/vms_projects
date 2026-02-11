import os
import re
import logging
from fastapi import FastAPI, Depends, HTTPException, Request, APIRouter
from sqlalchemy.orm import Session
from uvicorn.logging import DefaultFormatter
from starlette.requests import Request as StarletteRequest

from variamos_security import (load_keys, is_authenticated, has_roles, has_permissions, SessionUser, VariamosSecurityException, variamos_security_exception_handler)

import uuid
import time
from typing import Any, Dict, List, Optional, Literal
from urllib.parse import urlparse
import httpx
import asyncio
import random
from src.db_connector import get_db, SessionLocal
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from src.model.modelDAO import UserDao, ProjectDao
from pydantic import BaseModel, Field
from src.infrastructure.entry_points import (
    projects_admin_controller_v1,
    models_admin_controller_v1
)


router = APIRouter(prefix="/api/ai", tags=["ai"])

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

Role = Literal["system", "user", "assistant"]
# Configure logging
formatter = DefaultFormatter()
handler = logging.StreamHandler()
handler.setFormatter(formatter)

logging.basicConfig(level=logging.DEBUG, handlers=[handler])
logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

for noisy in ("httpx", "httpcore", "hpack", "h2"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger.setLevel(logging.INFO)

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


_openrouter_keys: List[str] = []
_openrouter_key_state: Dict[str, Dict[str, Any]] = {}  # {key: {"cooldown_until": float, "disabled_until": float}}
_openrouter_last_idx: int = 0
_openrouter_client: Optional[httpx.AsyncClient] = None
_openrouter_model_state: Dict[str, float] = {} 
_model_lock = asyncio.Lock()

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

OPENROUTER_KEYINFO_URL = "https://openrouter.ai/api/v1/key"
_openrouter_init_lock = asyncio.Lock()

def _mask_key(k: str) -> str:
    if not k:
        return "empty"
    if len(k) <= 10:
        return "***"
    return f"{k[:6]}…{k[-4:]}"

async def _ensure_openrouter_initialized():
    global _openrouter_keys, _openrouter_client

    if _openrouter_keys and _openrouter_client is not None:
        return

    async with _openrouter_init_lock:
        # re-check dentro del lock
        if not _openrouter_keys:
            _openrouter_keys = _load_openrouter_keys()
            logger.info("OpenRouter keys loaded: %d [%s]",
                        len(_openrouter_keys),
                        ", ".join(_mask_key(k) for k in _openrouter_keys))

        if _openrouter_client is None:
            timeout = httpx.Timeout(connect=10.0, read=65.0, write=10.0, pool=10.0)
            limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
            _openrouter_client = httpx.AsyncClient(timeout=timeout, limits=limits, http2=True)
            logger.info("OpenRouter client initialized.")

async def _check_key(api_key: str) -> dict:
    # Verifica que la key realmente sirve
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await _openrouter_client.get(OPENROUTER_KEYINFO_URL, headers=headers)
    return {"status": resp.status_code, "body": safe_json(resp)}


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


def _model_available(model: str, now: float) -> bool:
    until = _openrouter_model_state.get(model, 0.0)
    return now >= until

def _mark_model_cooldown(model: str, seconds: float):
    now = time.time()
    _openrouter_model_state[model] = max(_openrouter_model_state.get(model, 0.0), now + max(0.0, seconds))

def _build_openrouter_headers(request: Request, api_key: str) -> Dict[str, str]:
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

async def _pick_key_round_robin(now: float) -> Optional[str]:
    global _openrouter_last_idx

    if not _openrouter_keys:
        return None

    n = len(_openrouter_keys)

    async with _openrouter_rr_lock:
        start = _openrouter_last_idx % n
        for i in range(n):
            k = _openrouter_keys[(start + i) % n]
            if _key_available_now(k, now):
                _openrouter_last_idx = (start + i + 1) % n
                return k

    return None

def _openrouter_body_error(data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict) and err.get("message"):
        return {
            "code": err.get("code"),
            "message": err.get("message"),
            "metadata": err.get("metadata"),
        }
    return None

def _as_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return default
    
def _normalize_error_code(code: Any, default: int = 500) -> int:
    if code is None:
        return default
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        s = code.strip().lower()
        if s.isdigit():
            return int(s)
        # mapeos comunes
        if "rate" in s or "limit" in s:
            return 429
        if "insufficient" in s or "credit" in s or s == "payment_required":
            return 402
        if "unauthorized" in s:
            return 401
        if "forbidden" in s:
            return 403
        if "bad_request" in s:
            return 400
    return default


async def _client_gone(request: StarletteRequest) -> bool:
    try:
        return await request.is_disconnected()
    except Exception:
        return False
    
def _filter_models_by_availability(payload: Dict[str, Any], now: float) -> Dict[str, Any]:
    # construye lista candidata
    candidates: List[str] = []
    if isinstance(payload.get("models"), list):
        candidates = [m for m in payload["models"] if isinstance(m, str)]
    elif isinstance(payload.get("model"), str):
        candidates = [payload["model"]]

    # filtra por cooldown
    available = [m for m in candidates if _model_available(m, now)]
    if not available:
        return {}

    # reconstruye payload consistente
    p2 = dict(payload)
    p2["model"] = available[0]
    if len(available) > 1:
        p2["models"] = available
        p2["route"] = "fallback"
    else:
        p2.pop("models", None)
        p2.pop("route", None)
    return p2


def _seconds_until_any_model_available(payload: Dict[str, Any], now: float) -> float:
    candidates: List[str] = []
    if isinstance(payload.get("models"), list):
        candidates = [m for m in payload["models"] if isinstance(m, str)]
    elif isinstance(payload.get("model"), str):
        candidates = [payload["model"]]

    waits = []
    for m in candidates:
        until = _openrouter_model_state.get(m, 0.0)
        if until > now:
            waits.append(until - now)
    return min(waits) if waits else 0.0

def _candidate_models(payload: Dict[str, Any]) -> List[str]:
    ms = payload.get("models")
    if isinstance(ms, list) and ms:
        return [m for m in ms if isinstance(m, str)]
    m = payload.get("model")
    return [m] if isinstance(m, str) else []
    



def _backoff_seconds(attempt_idx: int, base: float = 1.0, cap: float = 12.0) -> float:
    t = min(cap, base * (2 ** attempt_idx))
    # jitter +-25%
    return t * (0.75 + random.random() * 0.5)


def _openrouter_any_error(data: Any) -> Optional[Dict[str, Any]]:
    """
    OpenRouter a veces responde HTTP 200 pero con error:
      - error top-level: {"error": {...}}
      - error dentro de choices[0]: {"choices":[{"error": {...}, ...}]}
      - error dentro de choices[0].message: {"choices":[{"message":{"error": {...}}}]}
    Retorna dict normalizado: {where, code, message, metadata, raw}
    """
    if not isinstance(data, dict):
        return None

    # 1) Top-level error
    err = data.get("error")
    if isinstance(err, dict) and (err.get("message") or err.get("code") is not None):
        return {
            "where": "top_level",
            "code": err.get("code"),
            "message": err.get("message") or "",
            "metadata": err.get("metadata"),
            "raw": err,
        }

    # 2) choices-level
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0] if isinstance(choices[0], dict) else {}

        c0_err = c0.get("error")
        if isinstance(c0_err, dict) and (c0_err.get("message") or c0_err.get("code") is not None):
            return {
                "where": "choices[0].error",
                "code": c0_err.get("code"),
                "message": c0_err.get("message") or "",
                "metadata": c0_err.get("metadata"),
                "raw": c0_err,
            }

        msg = c0.get("message")
        if isinstance(msg, dict):
            msg_err = msg.get("error")
            if isinstance(msg_err, dict) and (msg_err.get("message") or msg_err.get("code") is not None):
                return {
                    "where": "choices[0].message.error",
                    "code": msg_err.get("code"),
                    "message": msg_err.get("message") or "",
                    "metadata": msg_err.get("metadata"),
                    "raw": msg_err,
                }

    return None


async def call_openrouter_best_effort(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    PROXY LIMPIO (sin fallback de modelos):
      - 1 modelo por request
      - rota keys si una key está rate-limited o rechazada
      - aplica throttle local para evitar 429 en OpenRouter
      - devuelve error claro al front (para que el front decida fallback de modelo)
    """
    await _ensure_openrouter_initialized()
    if not _openrouter_keys:
        raise HTTPException(status_code=500, detail="No OpenRouter API keys configured")

    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(status_code=400, detail={"error": {"message": "Missing 'model' in payload"}})
    model = model.strip()

    payload2 = {
        "model": model,
        "messages": payload.get("messages") or [],
        "stream": False,
    }
    if isinstance(payload.get("user"), str) and payload["user"].strip():
        payload2["user"] = payload["user"].strip()
    max_key_tries = min(len(_openrouter_keys), 10)
    last_exc: Optional[HTTPException] = None

    for _ in range(max_key_tries):
        now_epoch = time.time()
        api_key = await _pick_key_round_robin(now_epoch)

        # Si hay keys pero todas están en cooldown/disabled, responde 429 con retryAfter
        if not api_key:
            wait_s = _seconds_until_any_key_available(time.time()) or 1.0
            raise HTTPException(
                status_code=429,
                detail={"error": {"message": "No API key available (cooldown)", "retryAfter": wait_s}},
                headers={"Retry-After": str(int(max(1.0, wait_s)))},
            )

        # throttle local
        if model.endswith(":free"):
            await _free_global_limiter.acquire()
            await _limiter_for_key(api_key).acquire()

        try:
            async with _openrouter_sem:
                resp = await _openrouter_post_with_key(payload2, request, api_key)
                body = safe_json(resp)
                logger.info(
                "[openrouter] status=%s model=%s key=%s retry-after=%s body=%s",
                resp.status_code,
                model,
                _mask_key(api_key),
                resp.headers.get("retry-after"),
                _summarize_openrouter_data(body) if isinstance(body, dict) else str(body)[:300]
                )
        except httpx.TimeoutException:
            _mark_cooldown(api_key, 3.0)
            last_exc = HTTPException(status_code=504, detail={"error": {"message": "OpenRouter timeout"}})
            continue
        except httpx.RequestError as e:
            _mark_cooldown(api_key, 3.0)
            last_exc = HTTPException(status_code=502, detail={"error": {"message": f"Network error: {str(e)}"}})
            continue

        # HTTP != 200
        if resp.status_code != 200:
            body = safe_json(resp)
            msg = ""
            if isinstance(body, dict):
                msg = str((body.get("error") or {}).get("message") or "")
            if not msg:
                msg = str(body)[:400]

            if resp.status_code == 429:
                wait_s = _retry_after_seconds(resp) or 8.0
                _mark_cooldown(api_key, wait_s)
                _mark_model_cooldown(model, wait_s)
                last_exc = HTTPException(
                    status_code=429,
                    detail={"error": {"message": msg or "Rate limited", "retryAfter": wait_s}},
                    headers={"Retry-After": str(int(wait_s))},
                )
                continue

            if resp.status_code in (401, 403):
                _mark_disabled(api_key, 120.0)
                last_exc = HTTPException(status_code=502, detail={"error": {"message": msg or "OpenRouter key rejected"}})
                continue

            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail={"error": {"message": msg or "Model not found"}})

            if resp.status_code in (400, 422):
                raise HTTPException(status_code=400, detail={"error": {"message": msg or "Invalid request"}})

            _mark_cooldown(api_key, 5.0)
            last_exc = HTTPException(status_code=502, detail={"error": {"message": msg or "Upstream error"}})
            continue

        # HTTP 200: puede traer error embebido
        data = safe_json(resp)
        or_err = _openrouter_any_error(data)
        if or_err:
            code = _normalize_error_code(or_err.get("code"), 502)
            err_msg = str(or_err.get("message") or "OpenRouter embedded error")

            if code == 429:
                wait_s = _retry_after_seconds(resp) or 8.0
                _mark_cooldown(api_key, wait_s)
                last_exc = HTTPException(
                    status_code=429,
                    detail={"error": {"message": err_msg, "retryAfter": wait_s}},
                    headers={"Retry-After": str(int(wait_s))},
                )
                continue

            _mark_cooldown(api_key, 3.0)
            last_exc = HTTPException(
                status_code=502,
                detail={"error": {"message": err_msg, "where": or_err.get("where"), "metadata": or_err.get("metadata")}},
            )
            continue

        return data

    if last_exc:
        raise last_exc

    raise HTTPException(status_code=503, detail={"error": {"message": "OpenRouter unavailable"}})


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

        timeout = httpx.Timeout(connect=10.0, read=65.0, write=10.0, pool=10.0)
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
        _openrouter_client = httpx.AsyncClient(timeout=timeout, limits=limits, http2=False)
        logger.info("OpenRouter client initialized.")
    except Exception as e:
        _openrouter_client = None
        _openrouter_keys = []
        logger.exception(f"OpenRouter init failed (service will still run): {e}")
    await _ensure_openrouter_initialized()


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


from collections import deque

OPENROUTER_MAX_CONCURRENCY = 3   
OPENROUTER_MAX_ATTEMPTS = 1      
OPENROUTER_MAX_TOTAL_WAIT_S = 60 
OPENROUTER_FREE_RPM = 10         
OPENROUTER_FREE_GLOBAL_RPM = 20  


_openrouter_sem = asyncio.Semaphore(OPENROUTER_MAX_CONCURRENCY)
_openrouter_rr_lock = asyncio.Lock()

class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._lock = asyncio.Lock()
        self._calls = deque()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                # limpia llamadas viejas
                while self._calls and (now - self._calls[0]) >= self.window_seconds:
                    self._calls.popleft()

                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return

                wait_s = self.window_seconds - (now - self._calls[0])

            await asyncio.sleep(max(0.0, wait_s))

_free_rpm_limiters: Dict[str, SlidingWindowRateLimiter] = {}
_free_global_limiter = SlidingWindowRateLimiter(OPENROUTER_FREE_GLOBAL_RPM, 60.0)

def _limiter_for_key(api_key: str) -> SlidingWindowRateLimiter:
    lim = _free_rpm_limiters.get(api_key)
    if lim is None:
        lim = SlidingWindowRateLimiter(OPENROUTER_FREE_RPM, 60.0)
        _free_rpm_limiters[api_key] = lim
    return lim

def _payload_uses_free_models(payload: Dict[str, Any]) -> bool:
    ms = payload.get("models")
    if isinstance(ms, list) and ms:
        return any(isinstance(m, str) and m.endswith(":free") for m in ms)
    m = payload.get("model")
    return isinstance(m, str) and m.endswith(":free")


class ChatMessage(BaseModel):
    role: Role
    content: str

class AIChatRequest(BaseModel):
    primaryModelId: str = Field(..., min_length=1)
    fallbackModelIds: List[str] = []
    messages: List[ChatMessage] = Field(..., min_length=1)

class AIChatResult(BaseModel):
    content: str
    usedModelId: str

@router.get("/_debug/openrouter", dependencies=[Depends(is_authenticated)])
async def debug_openrouter():
    await _ensure_openrouter_initialized()
    info = []
    for k in _openrouter_keys:
        try:
            r = await _check_key(k)
        except Exception as e:
            r = {"status": "error", "body": str(e)}
        info.append({"key": _mask_key(k), "check": r})
    return {
        "keys_loaded": len(_openrouter_keys),
        "client_ready": _openrouter_client is not None,
        "keys": info,
    }

def safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return resp.text[:2000]

def _seconds_until_any_key_available(now: float) -> float:
    waits = []
    for k in _openrouter_keys:
        st = _openrouter_key_state.get(k, {})
        cd = float(st.get("cooldown_until", 0.0) or 0.0)
        dis = float(st.get("disabled_until", 0.0) or 0.0)
        until = max(cd, dis)
        if until > now:
            waits.append(until - now)
    return min(waits) if waits else 0.0


def _truncate(s: str, n: int = 300) -> str:
    if not isinstance(s, str):
        return ""
    s = s.replace("\n", "\\n")
    return s[:n] + ("…" if len(s) > n else "")

def _summarize_openrouter_data(data: Any) -> dict:
    """
    Resumen seguro y chico (para logs) del response de OpenRouter.
    No loguea contenido completo ni metadata gigante.
    """
    if not isinstance(data, dict):
        return {"type": type(data).__name__}

    summary: Dict[str, Any] = {
        "keys": list(data.keys())[:25],
        "model": data.get("model"),
    }

    choices = data.get("choices")
    if isinstance(choices, list):
        summary["choices_len"] = len(choices)
        if choices:
            c0 = choices[0] if isinstance(choices[0], dict) else {}
            msg = c0.get("message") if isinstance(c0.get("message"), dict) else None
            if msg is None and isinstance(c0.get("delta"), dict):
                msg = c0.get("delta")

            raw_content = None
            if isinstance(msg, dict):
                raw_content = msg.get("content")
                # Choice-level error
            if isinstance(choices, list) and choices:
                c0 = choices[0] if isinstance(choices[0], dict) else {}
                c0err = c0.get("error")
                if isinstance(c0err, dict):
                    summary["choice0_error"] = {
                        "code": c0err.get("code"),
                        "message": _truncate(str(c0err.get("message") or ""), 250),
                    }
                    meta = c0err.get("metadata")
                    if meta is not None:
                        summary["choice0_error_meta_preview"] = _truncate(str(meta), 400)


            summary["choice0_keys"] = list(c0.keys())[:25]
            summary["raw_content_type"] = type(raw_content).__name__

            # preview del texto parseado (sin romper si falla)
            try:
                parsed = extract_text_content(data)
                summary["parsed_len"] = len(parsed or "")
                summary["parsed_preview"] = _truncate(parsed or "", 250)
            except Exception as e:
                summary["parsed_error"] = str(e)[:200]

    # Si viene error embebido
    err = data.get("error")
    if isinstance(err, dict):
        summary["error"] = {
            "code": err.get("code"),
            "message": _truncate(str(err.get("message") or ""), 250),
        }

    return summary


def extract_used_model(data: dict, fallback: str = "unknown") -> str:
    if not isinstance(data, dict):
        return fallback
    m = data.get("model")
    if isinstance(m, str) and m.strip():
        return m.strip()

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0] if isinstance(choices[0], dict) else {}
        m2 = ch0.get("model")
        if isinstance(m2, str) and m2.strip():
            return m2.strip()

        msg = ch0.get("message") if isinstance(ch0.get("message"), dict) else {}
        m3 = msg.get("model")
        if isinstance(m3, str) and m3.strip():
            return m3.strip()

    return fallback

def extract_text_content(data: dict) -> str:
    if not isinstance(data, dict):
        logger.warning("[openrouter] data is not dict: %s", type(data))
        return ""

    choices = data.get("choices") or []
    if not isinstance(choices, list) or not choices:
        ot = data.get("output_text")
        if isinstance(ot, str) and ot.strip():
            return ot.strip()

        logger.warning("[openrouter] no choices. keys=%s", sorted(list(data.keys()))[:30])
        return ""

    c0 = choices[0] if isinstance(choices[0], dict) else {}
    # OpenAI-style
    msg = c0.get("message")
    if msg is None:
        msg = c0.get("delta")

    if msg is None:
        txt = c0.get("text")
        if isinstance(txt, str) and txt.strip():
            return txt.strip()
        msg = {}

    # msg debería ser dict
    if not isinstance(msg, dict):
        logger.warning("[openrouter] message/delta not dict. type=%s", type(msg))
        return ""

    c = msg.get("content")

    # Caso normal: string
    if isinstance(c, str):
        return c.strip()

    # Caso: lista de partes [{type:"text", text:"..."}]
    if isinstance(c, list):
        parts = []
        for item in c:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        return "\n".join(parts).strip()

    # Caso: dict con text
    if isinstance(c, dict):
        t = c.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()

    # Caso: algunos devuelven output_text
    ot = data.get("output_text")
    if isinstance(ot, str) and ot.strip():
        return ot.strip()

    return ""

@router.post("/chat", response_model=AIChatResult)
async def chat(request: Request, req: AIChatRequest):
    model = req.primaryModelId

    user_obj = getattr(getattr(request, "state", None), "user", None)
    stable_user = str(user_obj.id) if user_obj and getattr(user_obj, "id", None) else None

    payload = {
        "model": model,
        "messages": [m.model_dump() for m in req.messages],
        "stream": False,
    }
    if stable_user:
        payload["user"] = stable_user

    data = await call_openrouter_best_effort(payload, request)

    content = extract_text_content(data)
    used_model = extract_used_model(data, fallback=model)

    if not (content or "").strip():
        raise HTTPException(status_code=502, detail={"error": "Empty content from OpenRouter", "usedModelId": used_model})

    return {"content": content, "usedModelId": used_model}




app.include_router(router)
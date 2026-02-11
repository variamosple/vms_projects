import os
import re
import logging
from fastapi import FastAPI, Depends, HTTPException, Request, APIRouter
from sqlalchemy.orm import Session
from uvicorn.logging import DefaultFormatter

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


async def call_openrouter_best_effort(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    1) NO hace fan-out por modelos: manda 1 request con `models` (fallback server-side)
    2) Throttle a 20 RPM si es :free
    3) Concurrency limitada
    4) Reintenta internamente 429/5xx/timeouts y rota keys si 401/403/402
    5) Maneja el caso especial: HTTP 200 pero viene {"error": ...} en el body
    """
    await _ensure_openrouter_initialized()
    if not _openrouter_keys:
        raise HTTPException(status_code=500, detail="No OpenRouter API keys configured")

    deadline = time.monotonic() + OPENROUTER_MAX_TOTAL_WAIT_S
    last_err: Any = None

    for attempt in range(OPENROUTER_MAX_ATTEMPTS):
        if time.monotonic() >= deadline:
            break

        now = time.time()
        api_key = await _pick_key_round_robin(now)

        if not api_key:
            # todas en cooldown/disabled -> espera un poco hasta que alguna quede libre
            wait_next = _seconds_until_any_key_available(time.time())
            sleep_s = min(1.0, wait_next) if wait_next > 0 else 0.25
            await asyncio.sleep(sleep_s)
            continue

        # rate limit (especialmente para :free)
        if _payload_uses_free_models(payload):
            await _free_rpm_limiter.acquire()

        try:
            logger.info(
                "OpenRouter payload: model=%s models=%s route=%s user=%s",
                payload.get("model"),
                payload.get("models"),
                payload.get("route"),
                payload.get("user"),
            )
            logger.info("OpenRouter key used: %s", _mask_key(api_key))
            async with _openrouter_sem:
                resp = await _openrouter_post_with_key(payload, request, api_key)
        except httpx.TimeoutException:
            _mark_cooldown(api_key, 1.5)
            last_err = {"type": "timeout", "attempt": attempt + 1}
            continue
        except httpx.RequestError as e:
            _mark_cooldown(api_key, 1.5)
            last_err = {"type": "network", "attempt": attempt + 1, "msg": str(e)}
            continue

        # =========================
        # Caso 200: puede venir error en el body
        # =========================
        if resp.status_code == 200:
            data = safe_json(resp)

            body_err = _openrouter_body_error(data)  # <- debe devolver dict o None
            if body_err:
                code = _normalize_error_code(body_err.get("code"), 500)
                err_msg = str(body_err.get("message") or "")

                # Tratar el "error en body" como si fuera status real
                if code == 429:
                    ra = _retry_after_seconds(resp)
                    wait_s = ra if ra > 0 else 2.5
                    wait_s = wait_s * (0.75 + random.random() * 0.5)

                    # ✅ SOLO la key que falló
                    _mark_cooldown(api_key, wait_s)

                    remaining = max(0.0, deadline - time.monotonic())
                    await asyncio.sleep(min(wait_s, remaining))
                    last_err = {"type": "429", "wait": wait_s, "msg": err_msg, "body_error": body_err, "key": _mask_key(api_key)}
                    continue


                if code == 402:
                    _mark_disabled(api_key, 3600.0)
                    last_err = {"type": "disabled_key", "status": 402, "msg": err_msg, "body_error": body_err}
                    continue

                if 500 <= code <= 599:
                    _mark_cooldown(api_key, 1.0 + attempt * 0.5)
                    await asyncio.sleep(0.2 + random.random() * 0.3)
                    last_err = {"type": "5xx", "status": code, "msg": err_msg, "body_error": body_err}
                    continue

                # otros códigos (400/422/etc) vienen como "error en body"
                if code in (400, 422):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"message": err_msg, "payload_hint": "Invalid request to OpenRouter", "body_error": body_err}},
                    )

                # resto: trátalo como 4xx genérico y reintenta con cooldown corto
                _mark_cooldown(api_key, 2.0)
                await asyncio.sleep(0.15 + random.random() * 0.2)
                last_err = {"type": "4xx", "status": code, "msg": err_msg, "body_error": body_err}
                continue
            content = extract_text_content(data)
            if not content.strip():
                _mark_cooldown(api_key, 1.0)
                last_err = {"type": "no_content", "attempt": attempt + 1, "used_model": data.get("model")}
                continue

            return data

        body = safe_json(resp)
        err_msg = ""
        if isinstance(body, dict):
            err_msg = str((body.get("error") or {}).get("message") or "")
        else:
            err_msg = str(body)[:300]

        if resp.status_code == 429:
            ra = _retry_after_seconds(resp)
            wait_s = ra if ra > 0 else 2.5
            wait_s = wait_s * (0.75 + random.random() * 0.5)

            # ✅ SOLO la key que falló
            _mark_cooldown(api_key, wait_s)

            remaining = max(0.0, deadline - time.monotonic())
            await asyncio.sleep(min(wait_s, remaining))
            last_err = {"type": "429", "wait": wait_s, "msg": err_msg, "key": _mask_key(api_key)}
            continue


        # 5xx => reintenta
        if 500 <= resp.status_code <= 599:
            _mark_cooldown(api_key, 1.0 + attempt * 0.5)
            await asyncio.sleep(0.2 + random.random() * 0.3)
            last_err = {"type": "5xx", "status": resp.status_code, "msg": err_msg}
            continue

        if resp.status_code in (401, 403, 402):
            _mark_disabled(api_key, 60.0)
            last_err = {"type": "disabled_key", "status": resp.status_code, "msg": err_msg}
            continue

        if resp.status_code in (400, 422):
            raise HTTPException(
                status_code=400,
                detail={"error": {"message": err_msg, "payload_hint": "Invalid request to OpenRouter"}},
            )

        _mark_cooldown(api_key, 2.0)
        await asyncio.sleep(0.15 + random.random() * 0.2)
        last_err = {"type": "4xx", "status": resp.status_code, "msg": err_msg}
        continue

    if isinstance(last_err, dict):
        t = last_err.get("type")

        if t == "429":
            retry = int(last_err.get("wait", 5) or 5)
            raise HTTPException(
                status_code=429,
                detail={"error": {"message": "Rate limited by OpenRouter", "last": last_err}},
                headers={"Retry-After": str(retry)},
            )

        if t == "disabled_key" and last_err.get("status") == 402:
            raise HTTPException(
                status_code=402,
                detail={"error": {"message": "OpenRouter: insufficient credits (402). Add credits and retry.", "last": last_err}},
            )

    raise HTTPException(
        status_code=503,
        detail={"error": {"message": "OpenRouter unavailable", "last": last_err}},
    )

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
        _openrouter_client = httpx.AsyncClient(timeout=timeout, limits=limits, http2=True)
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

OPENROUTER_FREE_RPM = 20
OPENROUTER_MAX_CONCURRENCY = 5
OPENROUTER_MAX_TOTAL_WAIT_S = 60  # < 30s front (si no usas streaming)
OPENROUTER_MAX_ATTEMPTS = 6

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

_free_rpm_limiter = SlidingWindowRateLimiter(OPENROUTER_FREE_RPM, 60.0)

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


def _truncate(s: str, n: int = 400) -> str:
    if not isinstance(s, str):
        return ""
    s = s.replace("\n", "\\n")
    return s[:n] + ("…" if len(s) > n else "")

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
        return ""

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        # Algunas respuestas podrían usar "output_text" u otros campos
        ot = data.get("output_text")
        return ot.strip() if isinstance(ot, str) else ""

    ch0 = choices[0] if isinstance(choices[0], dict) else {}

    msg = ch0.get("message") if isinstance(ch0.get("message"), dict) else {}
    c = msg.get("content")

    # 1) content string
    if isinstance(c, str):
        return c.strip()

    if isinstance(c, list):
        parts = []
        for item in c:
            if not isinstance(item, dict):
                continue
            # variantes típicas: {"type":"text","text":"..."} o {"type":"output_text","text":"..."}
            t = item.get("text")
            if isinstance(t, str) and t.strip():
                parts.append(t.strip())
            t2 = item.get("content")
            if isinstance(t2, str) and t2.strip():
                parts.append(t2.strip())
        return "\n".join(parts).strip()

    if isinstance(c, dict):
        t = c.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()
        # algunas variantes usan {"content":"..."}
        t2 = c.get("content")
        if isinstance(t2, str) and t2.strip():
            return t2.strip()

    t = ch0.get("text")
    if isinstance(t, str) and t.strip():
        return t.strip()

    delta = ch0.get("delta")
    if isinstance(delta, dict):
        dc = delta.get("content")
        if isinstance(dc, str) and dc.strip():
            return dc.strip()

    return ""

@router.post("/chat", response_model=AIChatResult)
async def chat(request: Request, req: AIChatRequest):
    models = [req.primaryModelId] + [m for m in req.fallbackModelIds if m and m != req.primaryModelId]
    models = models[:3]

    user_obj = getattr(getattr(request, "state", None), "user", None)
    stable_user = str(user_obj.id) if user_obj and getattr(user_obj, "id", None) else None

    payload = {
        "model": models[0],
        "messages": [m.model_dump() for m in req.messages],
        "stream": False,
    }
    if len(models) > 1:
        payload["models"] = models
        payload["route"] = "fallback"
    if stable_user:
        payload["user"] = stable_user

    logger.info("[/api/ai/chat] -> sending to OpenRouter | primary=%s fallbacks=%s user=%s",
                models[0], models[1:], stable_user)

    data = await call_openrouter_best_effort(payload, request)
    try:
        keys = list(data.keys()) if isinstance(data, dict) else []
        nchoices = len(data.get("choices") or []) if isinstance(data, dict) else 0
        logger.info("[OpenRouter] <- response keys=%s choices=%s model(top)=%s",
                    keys, nchoices, data.get("model") if isinstance(data, dict) else None)
    except Exception:
        logger.exception("[OpenRouter] log summary failed")

    content = extract_text_content(data)
    used_model = extract_used_model(data, fallback=(models[0] if models else "unknown"))

    try:
        ch0 = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
        msg = ch0.get("message") if isinstance(ch0, dict) else {}
        raw_c = msg.get("content") if isinstance(msg, dict) else None
        logger.info("[OpenRouter] parsed | used_model=%s | content_len=%s | raw_content_type=%s | preview=%s",
                    used_model, len(content or ""), type(raw_c).__name__, _truncate(content, 300))
    except Exception:
        logger.exception("[OpenRouter] log parsed failed")

    if not (content or "").strip():
        try:
            logger.error("[OpenRouter] EMPTY content after parsing | used_model=%s | data_preview=%s",
                         used_model, _truncate(str(data), 800))
        except Exception:
            pass
        raise HTTPException(status_code=502, detail={"error": "Empty content from OpenRouter", "usedModelId": used_model})

    return {"content": content, "usedModelId": used_model}




app.include_router(router)
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src.multimodel_validation import ValidationResult, validate_snapshot


router = APIRouter(prefix="/api/ai", tags=["ai"])
_MAX_CAPTURE_BYTES = 1024 * 1024
_MAX_JSON_VALUES = 20_000
_NO_STORE = {"Cache-Control": "no-store"}


def _capture_too_large() -> HTTPException:
    return HTTPException(413, "La captura supera el tamaño admitido para la validación.", headers=_NO_STORE)


def _evaluate(body: bytearray) -> ValidationResult:
    capture = json.loads(body)
    pending = [capture]
    remaining = _MAX_JSON_VALUES
    while pending:
        value = pending.pop()
        remaining -= 1
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        if len(pending) > remaining:
            raise _capture_too_large()
    return validate_snapshot(capture)


@router.post("/validate-snapshot")
async def validate_model_snapshot(request: Request) -> JSONResponse:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_CAPTURE_BYTES:
            raise _capture_too_large()
        body.extend(chunk)

    try:
        report = await run_in_threadpool(_evaluate, body)
    except (ValueError, RecursionError):
        raise HTTPException(422, "La captura JSON no cumple el contrato de validación.", headers=_NO_STORE) from None
    return JSONResponse(report, headers=_NO_STORE)

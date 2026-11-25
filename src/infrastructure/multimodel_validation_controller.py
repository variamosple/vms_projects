import json
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src.multimodel_validation import ValidationResult, validate_snapshot
from src.multimodel_validation.transition import TransitionLimitExceeded, validate_transition
from src.multimodel_validation.transition_contracts import TransitionResult


router = APIRouter(prefix="/api/ai", tags=["ai"])
_MAX_CAPTURE_BYTES = 1024 * 1024
_MAX_JSON_VALUES = 20_000
_MAX_TRANSITION_BYTES = 2 * _MAX_CAPTURE_BYTES
_MAX_TRANSITION_VALUES = 2 * _MAX_JSON_VALUES
_MAX_TRANSITION_IMPACTS = 10_000
_NO_STORE = {"Cache-Control": "no-store"}


def _capture_too_large() -> HTTPException:
    return HTTPException(413, "La captura supera el tamaño admitido para la validación.", headers=_NO_STORE)


def _parse_capture(body: bytearray, max_values: int) -> Any:
    capture = json.loads(body)
    pending = [capture]
    remaining = max_values
    while pending:
        value = pending.pop()
        remaining -= 1
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        if len(pending) > remaining:
            raise _capture_too_large()
    return capture


def _evaluate(body: bytearray) -> ValidationResult:
    return validate_snapshot(_parse_capture(body, _MAX_JSON_VALUES))


def _evaluate_transition(body: bytearray) -> TransitionResult:
    try:
        return validate_transition(_parse_capture(body, _MAX_TRANSITION_VALUES), max_impacts=_MAX_TRANSITION_IMPACTS)
    except TransitionLimitExceeded:
        raise _capture_too_large() from None


async def _validate(request: Request, evaluate: Callable[[bytearray], Any], max_bytes: int) -> JSONResponse:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise _capture_too_large()
        body.extend(chunk)

    try:
        report = await run_in_threadpool(evaluate, body)
    except (ValueError, RecursionError):
        raise HTTPException(422, "La captura JSON no cumple el contrato de validación.", headers=_NO_STORE) from None
    return JSONResponse(report, headers=_NO_STORE)


@router.post("/validate-snapshot")
async def validate_model_snapshot(request: Request) -> JSONResponse:
    return await _validate(request, _evaluate, _MAX_CAPTURE_BYTES)


@router.post("/validate-transition")
async def validate_model_transition(request: Request) -> JSONResponse:
    return await _validate(request, _evaluate_transition, _MAX_TRANSITION_BYTES)

"""Explicit trace records and typed links; user prompts are not part of this contract."""

import json
from typing import Any

from .correspondence_profile import _list, _object, _text


def trace_id(value: Any) -> Any:
    from .snapshot import identifier

    return value if identifier(value) is not None and len(value) <= 256 else None


def _id(value: Any) -> None:
    if trace_id(value) is None:
        raise ValueError("Los identificadores de trazabilidad deben ser texto válido de hasta 256 caracteres.")


def _entity(value: Any) -> None:
    _object(value, {"kind", "modelId", "id"}, set(), "entity")
    for field in ("kind", "modelId", "id"):
        _text(value[field], "entity." + field)
    if value["kind"] not in ("model", "element", "relationship"):
        raise ValueError("Tipo de artefacto no soportado.")


def _reference(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("La referencia debe ser un objeto explícito.")
    kind = value.get("kind")
    if kind == "change":
        _object(value, {"kind", "index"}, set(), "change reference")
        if type(value["index"]) is not int or value["index"] < 0:
            raise ValueError("El índice del cambio debe ser un entero no negativo.")
    elif kind == "artifact":
        _object(value, {"kind", "phase", "entity"}, set(), "artifact reference")
        if value["phase"] not in ("before", "after"):
            raise ValueError("La referencia debe indicar before o after.")
        _entity(value["entity"])
    elif kind == "version":
        _object(value, {"kind", "modelId", "versionId"}, set(), "version reference")
        _text(value["modelId"], "modelId")
        _text(value["versionId"], "versionId")
    elif kind == "origin":
        _object(value, {"kind", "catalog", "id"}, set(), "origin reference")
        if value["catalog"] not in ("attempt", "context"):
            raise ValueError("El origen debe indicar su catálogo attempt o context.")
        _text(value["id"], "origin.id")
    elif kind in ("history", "requirement"):
        _object(value, {"kind", "id"}, set(), "record reference")
        _text(value["id"], "record.id")
    else:
        raise ValueError("Clase de referencia de trazabilidad no soportada.")


def validate_attempt(value: Any, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    _object(value, {"schemaVersion", "id", "projectId", "beforeSnapshotId", "afterSnapshotId"}, {"origin"}, "attemptContext")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("attemptContext.schemaVersion debe ser 1.")
    _id(value["id"])
    if (value["projectId"] != after["projectId"] or value["beforeSnapshotId"] != before["snapshotId"]
            or value["afterSnapshotId"] != after["snapshotId"]):
        raise ValueError("El intento no corresponde a este proyecto y par de capturas.")
    if "origin" in value:
        origin = _object(value["origin"], {"id", "kind", "source"}, set(), "attempt.origin")
        for field in ("id", "kind", "source"):
            _text(origin[field], "attempt.origin." + field)
        _id(origin["id"])
        if origin["kind"] not in ("request", "interaction", "event"):
            raise ValueError("El origen debe ser una solicitud, interacción o evento.")
    return value


def validate_context(value: Any, project_id: str) -> dict[str, Any]:
    fields = {"schemaVersion", "id", "projectId", "scope", "origins", "requirements", "history", "links"}
    context = _object(value, fields, set(), "traceContext")
    if type(context["schemaVersion"]) is not int or context["schemaVersion"] != 1 or context["projectId"] != project_id:
        raise ValueError("El contexto debe tener versión 1 y pertenecer al proyecto de las capturas.")
    _id(context["id"])
    if context["scope"] not in ("complete", "partial"):
        raise ValueError("traceContext.scope debe ser complete o partial.")
    try:
        json.dumps(context, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("El contexto debe ser JSON finito y serializable.") from None
    for category in ("origins", "requirements", "history", "links"):
        entries = _list(context[category], category)
        if len(entries) > (512 if category in ("history", "links") else 256):
            raise ValueError("El catálogo de trazabilidad supera su límite de entradas.")
        seen_links = set()
        for entry in entries:
            if category == "origins":
                _object(entry, {"id", "projectId", "kind"}, {"source"}, category)
                if entry["kind"] not in ("request", "interaction", "event"):
                    raise ValueError("Tipo de origen no soportado.")
                if "source" in entry:
                    _text(entry["source"], "origin.source")
            elif category == "requirements":
                _object(entry, {"id", "projectId", "artifact"}, set(), category)
                _reference(entry["artifact"])
                if entry["artifact"]["kind"] != "artifact":
                    raise ValueError("Cada requerimiento debe mapear un artefacto explícito de la captura.")
            elif category == "history":
                required = {"id", "projectId", "modelId", "entityType", "entityId", "oldValue", "newValue"}
                optional = {"valueScope", "actionType", "entityName", "userId", "userName", "description", "createdAt", "fromVersionId", "toVersionId"}
                _object(entry, required, optional, category)
                for field in ("modelId", "entityType", "entityId"):
                    _text(entry[field], "history." + field)
                if entry.get("valueScope", "partial") not in ("entity", "partial"):
                    raise ValueError("valueScope debe ser entity o partial.")
            else:
                _object(entry, {"id", "relation", "source", "target"}, set(), category)
                _text(entry["relation"], "link.relation")
                _reference(entry["source"])
                _reference(entry["target"])
            _id(entry["id"])
            if category != "links":
                _text(entry["projectId"], category + ".projectId")
            elif entry["id"] in seen_links:
                raise ValueError("Los identificadores de enlaces deben ser únicos.")
            seen_links.add(entry["id"])
    return context

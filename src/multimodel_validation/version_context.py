"""Validate supplied version evidence without deriving versions from capture IDs."""

import json
from typing import Any

from .correspondence_profile import _list, _object, _text


def _revision_address(value: Any, path: str, artifact: bool = False) -> None:
    fields = {"modelId", "versionId"} | ({"kind", "id"} if artifact else set())
    _object(value, fields, set(), path)
    for field in fields:
        _text(value[field], path + "." + field)
    if artifact and value["kind"] not in ("model", "element", "relationship"):
        raise ValueError("La referencia debe identificar model, element o relationship.")


def validate_context(value: Any, project_id: str) -> dict[str, Any]:
    fields = {"schemaVersion", "id", "projectId", "scope", "historyScope", "revisions", "compatibility", "correspondences", "changes", "historicalReferences"}
    context = _object(value, fields, set(), "versionContext")
    if type(context["schemaVersion"]) is not int or context["schemaVersion"] != 1:
        raise ValueError("versionContext.schemaVersion debe ser 1.")
    _text(context["id"], "versionContext.id")
    if context["projectId"] != project_id:
        raise ValueError("El contexto histórico debe pertenecer al proyecto de la captura.")
    if context["scope"] not in ("complete", "partial") or context["historyScope"] not in ("complete", "partial"):
        raise ValueError("Declare scope e historyScope como complete o partial.")
    try:
        json.dumps(context, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("El contexto debe ser JSON finito y serializable.") from None
    ids: set[str] = set()
    limits = {"revisions": 128, "compatibility": 256, "correspondences": 256, "historicalReferences": 256, "changes": 512}
    for category, limit in limits.items():
        entries = _list(context[category], category)
        if len(entries) > limit:
            raise ValueError(f"{category}: supera el máximo de {limit} entradas.")
        for entry in entries:
            if category == "revisions":
                _object(entry, {"modelId", "versionId", "parentVersionIds", "scope", "model"}, set(), category)
                _text(entry["modelId"], "revision.modelId")
                _text(entry["versionId"], "revision.versionId")
                parents = _list(entry["parentVersionIds"], "parentVersionIds")
                if len(parents) > 4:
                    raise ValueError("Se admiten como máximo cuatro padres declarados por revisión.")
                for parent in parents:
                    _text(parent, "parentVersionId")
                if len(set(parents)) != len(parents):
                    raise ValueError("Los padres de una revisión no deben repetirse.")
                if entry["scope"] not in ("complete", "partial"):
                    raise ValueError("revision.scope debe ser complete o partial.")
                model = entry["model"]
                if model is not None and (not isinstance(model, dict) or model.get("id") != entry["modelId"]):
                    raise ValueError("La revisión debe contener su modelo correspondiente o una eliminación explícita null.")
                continue
            if category == "changes":
                required = {"id", "projectId", "modelId", "entityType", "entityId", "oldValue", "newValue"}
                optional = {"fromVersionId", "toVersionId", "valueScope", "actionType", "entityName", "userId", "userName", "description", "createdAt"}
                _object(entry, required, optional, category)
                for field in ("projectId", "modelId", "entityType", "entityId"):
                    _text(entry[field], "change." + field)
                for field in ("fromVersionId", "toVersionId"):
                    if field in entry and entry[field] is not None:
                        _text(entry[field], "change." + field)
                if entry.get("valueScope", "partial") not in ("entity", "partial"):
                    raise ValueError("valueScope debe ser entity o partial.")
            else:
                _object(entry, {"id", "source", "target"} | ({"compatible"} if category == "compatibility" else set()), set(), category)
                for field in ("source", "target"):
                    _revision_address(entry[field], category + "." + field, category != "compatibility")
                if category == "compatibility" and type(entry["compatible"]) is not bool:
                    raise ValueError("compatible debe ser booleano.")
                if category == "correspondences" and entry["source"]["modelId"] == entry["target"]["modelId"]:
                    raise ValueError("Las correspondencias deben conectar modelos distintos.")
            _text(entry["id"], category + ".id")
            if entry["id"] in ids:
                raise ValueError("Los identificadores de políticas, cambios y enlaces deben ser únicos.")
            ids.add(entry["id"])
    return context

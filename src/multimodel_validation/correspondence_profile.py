"""Validate a small declarative language. No expressions or executable code."""

import json
from typing import Any

from .snapshot import identifier


def _object(value: Any, required: set[str], optional: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise ValueError(f"{path}: campos ausentes o no admitidos.")
    return value


def _text(value: Any, path: str) -> None:
    if identifier(value) is None:
        raise ValueError(f"{path}: se requiere texto no vacío, sin espacios exteriores.")


def _list(value: Any, path: str, minimum: int = 0) -> list[Any]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ValueError(f"{path}: se requiere una lista con al menos {minimum} entradas.")
    return value


def _selector(value: Any, path: str, level: str = "element") -> None:
    selector = _object(value, set(), {"modelId", "languageId", "elementType"} if level == "element" else {"modelId", "languageId"}, path)
    if not selector.keys() & {"modelId", "languageId"}:
        raise ValueError(f"{path}: se requiere modelId o languageId para definir el ámbito.")
    for field, item in selector.items():
        if field == "languageId" and type(item) is int:
            continue
        _text(item, f"{path}.{field}")


def _attribute(value: Any, path: str) -> None:
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError(f"{path}: use property o path, de forma explícita.")
    if "property" in value:
        _text(value["property"], path)
    elif "path" in value:
        parts = _list(value["path"], path, 1)
        if len(parts) > 16:
            raise ValueError(f"{path}: la ruta supera 16 componentes.")
        for part in parts:
            _text(part, path)
    else:
        raise ValueError(f"{path}: descriptor de atributo no admitido.")


def _operator(value: Any, path: str) -> None:
    if value not in ("equals", "not_equals"):
        raise ValueError(f"{path}: solo se admite equals o not_equals.")


def _address(value: Any, path: str) -> None:
    _object(value, {"modelId", "elementId"}, set(), path)
    _text(value["modelId"], path)
    _text(value["elementId"], path)


def validate_profile(value: Any) -> dict[str, Any]:
    profile = _object(value, {"schemaVersion", "id", "correspondences", "dependencies", "requiredCorrespondences"}, set(), "perfil")
    if type(profile["schemaVersion"]) is not int or profile["schemaVersion"] != 1:
        raise ValueError("perfil.schemaVersion: debe ser 1.")
    _text(profile["id"], "perfil.id")
    ids: set[str] = set()
    for category in ("correspondences", "dependencies", "requiredCorrespondences"):
        for index, entry in enumerate(_list(profile[category], category)):
            path = f"{category}[{index}]"
            if category == "correspondences":
                _object(entry, {"id", "source", "target", "checks"}, {"level"}, path)
                level = entry.get("level", "element")
                if level not in ("model", "element"):
                    raise ValueError(f"{path}.level: debe ser model o element.")
                _selector(entry["source"], path + ".source", level)
                _selector(entry["target"], path + ".target", level)
                for check in _list(entry["checks"], path + ".checks", 1):
                    _object(check, {"source", "target", "operator"}, set(), path + ".checks")
                    _attribute(check["source"], path)
                    _attribute(check["target"], path)
                    _operator(check["operator"], path)
            elif category == "dependencies":
                _object(entry, {"id", "source", "target", "conditions"}, {"when", "requireOriginLink"}, path)
                _address(entry["source"], path + ".source")
                _address(entry["target"], path + ".target")
                if entry["source"]["modelId"] == entry["target"]["modelId"]:
                    raise ValueError(f"{path}: la dependencia debe ser entre modelos distintos.")
                if "requireOriginLink" in entry and type(entry["requireOriginLink"]) is not bool:
                    raise ValueError(f"{path}.requireOriginLink: debe ser booleano.")
                for field in ("conditions", "when"):
                    for condition in _list(entry.get(field, []), path + "." + field):
                        required = {"attribute", "operator", "value"} | ({"on"} if field == "conditions" else set())
                        _object(condition, required, set(), path + "." + field)
                        if field == "conditions" and condition["on"] not in ("source", "target"):
                            raise ValueError(f"{path}.conditions.on: debe ser source o target.")
                        _attribute(condition["attribute"], path)
                        _operator(condition["operator"], path)
            else:
                _object(entry, {"id", "source", "target"}, {"minimum", "maximum"}, path)
                _selector(entry["source"], path + ".source")
                _selector(entry["target"], path + ".target")
                minimum, maximum = entry.get("minimum", 1), entry.get("maximum")
                if type(minimum) is not int or minimum < 0 or (maximum is not None and (type(maximum) is not int or maximum < minimum)):
                    raise ValueError(f"{path}: cardinalidades inválidas.")
            _text(entry["id"], path + ".id")
            if entry["id"] in ids:
                raise ValueError("perfil: los identificadores de criterios deben ser únicos.")
            ids.add(entry["id"])
    try:
        json.dumps(profile, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("perfil: los valores deben ser JSON finito y serializable.") from None
    return profile

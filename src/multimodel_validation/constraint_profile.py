"""Explicit Boolean projection; never parses native language constraint text."""

import json
from typing import Any

from .correspondence import _equal
from .correspondence_profile import _attribute, _list, _object, _text


MAX_VARIABLES = 12
MAX_EXPRESSION_DEPTH = 12
MAX_EXPRESSION_NODES = 2048


def variables_in(expression: Any) -> set[str]:
    if type(expression) is bool:
        return set()
    operator, operand = next(iter(expression.items()))
    if operator == "var":
        return {operand}
    if operator == "not":
        return variables_in(operand)
    return set().union(*(variables_in(item) for item in operand))


def validate_profile(value: Any) -> dict[str, Any]:
    fields = {"schemaVersion", "id", "variables", "constraints", "dependencies", "conditions"}
    profile = _object(value, fields, set(), "constraintProfile")
    if type(profile["schemaVersion"]) is not int or profile["schemaVersion"] != 1:
        raise ValueError("constraintProfile.schemaVersion: debe ser 1.")
    _text(profile["id"], "constraintProfile.id")
    try:
        json.dumps(profile, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Los criterios deben ser JSON finito y serializable.") from None
    variables = _list(profile["variables"], "variables")
    if len(variables) > MAX_VARIABLES:
        raise ValueError(f"Se admiten como máximo {MAX_VARIABLES} variables booleanas explícitas.")
    declared: set[str] = set()
    for variable in variables:
        _object(variable, {"id", "bindings"}, set(), "variable")
        _text(variable["id"], "variable.id")
        if variable["id"] in declared:
            raise ValueError("Los identificadores de variables deben ser únicos.")
        declared.add(variable["id"])
        bindings = _list(variable["bindings"], "variable.bindings", 1)
        if len(bindings) > 16:
            raise ValueError("Se admiten como máximo 16 enlaces por variable.")
        seen = set()
        for binding in bindings:
            _object(binding, {"modelId", "attribute"}, {"elementId", "trueValue", "falseValue"}, "binding")
            _text(binding["modelId"], "binding.modelId")
            if "elementId" in binding:
                _text(binding["elementId"], "binding.elementId")
            _attribute(binding["attribute"], "binding.attribute")
            if ("trueValue" in binding) != ("falseValue" in binding):
                raise ValueError("Declare trueValue y falseValue juntos.")
            yes, no = binding.get("trueValue", True), binding.get("falseValue", False)
            if any(type(item) not in (str, int, float, bool, type(None)) for item in (yes, no)) or _equal(yes, no):
                raise ValueError("trueValue y falseValue deben ser escalares JSON tipados diferentes.")
            identity = json.dumps(binding, sort_keys=True)
            if identity in seen:
                raise ValueError("No se admiten enlaces de variable duplicados.")
            seen.add(identity)

    remaining = MAX_EXPRESSION_NODES

    def expression(item: Any, depth: int = 0) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > MAX_EXPRESSION_DEPTH:
            raise ValueError("Las expresiones superan el límite de tamaño o profundidad.")
        if type(item) is bool:
            return
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError("Use un booleano o una expresión explícita de un solo operador.")
        operator, operand = next(iter(item.items()))
        if operator == "var":
            _text(operand, "expression.var")
            if operand not in declared:
                raise ValueError("La expresión referencia una variable no declarada.")
        elif operator == "not":
            expression(operand, depth + 1)
        elif operator in ("and", "or", "implies"):
            items = _list(operand, "expression." + operator, 2)
            if operator == "implies" and len(items) != 2:
                raise ValueError("implies requiere exactamente dos operandos.")
            for child in items:
                expression(child, depth + 1)
        else:
            raise ValueError("Operador no admitido; use var, not, and, or o implies.")

    ids: set[str] = set()
    for category in ("constraints", "dependencies", "conditions"):
        entries = _list(profile[category], category)
        if len(entries) > 128:
            raise ValueError("Se admiten como máximo 128 criterios por categoría.")
        for entry in entries:
            if category == "dependencies":
                _object(entry, {"id", "sourceModelId", "targetModelId", "when", "requires"}, set(), category)
                for field in ("sourceModelId", "targetModelId"):
                    _text(entry[field], field)
                if entry["sourceModelId"] == entry["targetModelId"]:
                    raise ValueError("La dependencia debe conectar modelos distintos.")
                expression(entry["when"])
                expression(entry["requires"])
            else:
                required = {"id", "modelId", "expression"} | ({"conceptId"} if category == "conditions" else set())
                _object(entry, required, set(), category)
                _text(entry["modelId"], "modelId")
                if category == "conditions":
                    _text(entry["conceptId"], "conceptId")
                expression(entry["expression"])
            _text(entry["id"], "criterion.id")
            if entry["id"] in ids:
                raise ValueError("Los identificadores de criterios deben ser únicos.")
            ids.add(entry["id"])
    return profile

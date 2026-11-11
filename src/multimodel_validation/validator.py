import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from .contracts import Finding, Location, RuleId, RuleResult, Severity, Status
from .contracts import ValidationRequest, ValidationResult
from .snapshot import MISSING, RELATION_ENDPOINTS, Model, Record, Reference, Snapshot, identifier


_RULES: dict[RuleId, str] = {
    "RG01": "Existencia del elemento destino en la captura actual completa.",
    "RG02": "Integridad del identificador y del ámbito de la referencia.",
    "RG03": "Unicidad del identificador de elemento dentro de su modelo.",
    "RG04": "Compatibilidad del tipo de relación y sus extremos con la sintaxis abstracta.",
    "RG05": "Existencia e identificación de ambos extremos del enlace de origen declarado.",
}
_SEVERITY: dict[Status, Severity] = {
    "incumple": "error",
    "no_evaluable": "warning",
    "cumple": "info",
    "no_aplica": "info",
}


@dataclass(frozen=True)
class _Check:
    status: Status
    code: str
    description: str


_PASS = _Check("cumple", "resolved", "Comprobación satisfecha.")
_UNSUPPORTED_LINK = _Check(
    "no_evaluable", "unsupported_origin_format", "El enlace de origen tiene un formato no soportado."
)


def _check_identifier(value: Any) -> _Check:
    if value is MISSING:
        return _Check("no_evaluable", "missing_identifier", "Falta el campo del identificador.")
    if identifier(value) is None:
        return _Check("incumple", "invalid_identifier", "El identificador explícito es inválido.")
    return _PASS


def _lookup(
    records: dict[str, list[Any]], value: Any, complete: bool, entity: str
) -> tuple[_Check, Any]:
    check = _check_identifier(value)
    if check.status != "cumple":
        return check, None
    matches = records.get(value, [])
    if len(matches) == 1:
        return _PASS, matches[0]
    if matches:
        return _Check("incumple", f"ambiguous_{entity}", "El identificador tiene varios destinos."), None
    if complete:
        return _Check("incumple", f"missing_{entity}", "El destino no existe en su ámbito."), None
    return _Check("no_evaluable", "target_outside_coverage", "La captura no permite descartar el destino."), None


def _abstract_syntax(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, dict) and "abstractSyntax" in value:
        value = value["abstractSyntax"]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and all(
        isinstance(value.get(field), dict) for field in ("elements", "relationships")
    ):
        return value
    return None


def _language_key(value: Any) -> Optional[str]:
    return str(value) if type(value) is int else identifier(value)


class _Evaluation:
    def __init__(self, snapshot: Snapshot, rules: list[RuleId], declared_complete: bool):
        self.snapshot = snapshot
        self.rules = rules
        self.declared_complete = declared_complete
        self.incomplete_paths = snapshot.incomplete_paths
        self.complete = declared_complete and not self.incomplete_paths
        self.counts = {rule: Counter() for rule in rules}
        self.model_paths = {rule: {} for rule in rules}
        self.findings: list[Finding] = []

    def record(
        self, rules: tuple[RuleId, ...], check: _Check, path: str,
        model: Optional[Model] = None, *, element: Optional[Record] = None,
        relationship: Optional[Record] = None, evidence: Optional[dict[str, Any]] = None,
    ) -> None:
        selected = [rule for rule in rules if rule in self.counts]
        if not selected:
            return
        for rule in selected:
            self.counts[rule][check.status] += 1
            if model is not None:
                self.model_paths[rule][model.record.path] = None
        if check.status == "cumple":
            return
        location: Location = {
            "path": path,
            "modelId": model.record.id if model is not None else None,
            "elementId": element.id if element is not None else None,
            "referenceId": relationship.id if relationship is not None else None,
        }
        self.findings.append({
            "ruleIds": selected,
            "status": check.status,
            "severity": _SEVERITY[check.status],
            "code": check.code,
            "description": check.description,
            "location": location,
            "evidence": evidence or {},
        })

    def rule_results(self) -> list[RuleResult]:
        results: list[RuleResult] = []
        priority: tuple[Status, ...] = ("incumple", "no_evaluable", "cumple")
        for rule, counts in self.counts.items():
            status = next((state for state in priority if counts[state]), "no_aplica")
            results.append({
                "ruleId": rule, "status": status, "severity": _SEVERITY[status],
                "description": _RULES[rule], "checkCounts": dict(counts),
                "modelPaths": list(self.model_paths[rule]),
            })
        return results

    def evaluate(self, metamodels: dict[str, Any]) -> None:
        if not self.complete:
            self.record(("RG01",), _Check(
                "no_evaluable", "incomplete_snapshot", "RG01 requiere una captura actual completa."
            ), "/project", evidence={"incompletePaths": self.incomplete_paths})
        if self.snapshot.inventory_gaps:
            self.record(("RG02", "RG03", "RG04", "RG05"), _Check(
                "no_evaluable", "incomplete_inventory", "El inventario de modelos tiene ubicaciones sin cubrir."
            ), "/project", evidence={"incompletePaths": self.snapshot.inventory_gaps})
        syntax_by_language = {
            key: _abstract_syntax(value) for key, value in metamodels.items()
        } if "RG04" in self.counts else {}
        for model in self.snapshot.models:
            self._model(model, syntax_by_language.get(_language_key(model.record.data.get("languageId"))))

    def _model(self, model: Model, syntax: Optional[dict[str, Any]]) -> None:
        for gaps, rules in (
            (model.element_gaps, ("RG02", "RG03", "RG05")),
            (model.relationship_gaps, ("RG02", "RG04")),
        ):
            for path in gaps:
                self.record(rules, _Check(
                    "no_evaluable", "incomplete_model_data", "Faltan datos del modelo o su formato no es soportado."
                ), path, model)

        for element_id, elements in model.elements_by_id.items():
            check = _PASS if len(elements) == 1 else _Check(
                "incumple", "duplicate_element_id", "El ID del elemento se repite dentro del modelo."
            )
            self.record(("RG03",), check, f"{elements[0].path}/id", model, element=elements[0],
                        evidence={"elementId": element_id, "paths": [e.path for e in elements]})

        check_relationships = "RG04" in self.counts
        endpoints: dict[str, Optional[Record]] = {}
        for path, reference in model.reference_index().items():
            if reference.field in ("sourceModelIds", "sourceModelElements"):
                self._origin(model, path, reference)
            else:
                target = self._internal(model, path, reference)
                if check_relationships and reference.field in RELATION_ENDPOINTS:
                    endpoints[path] = target
        if check_relationships:
            for path, relationship in model.relationships.items():
                self._relationship_type(
                    model, relationship, syntax, endpoints[f"{path}/sourceId"], endpoints[f"{path}/targetId"]
                )

    def _element_reference(
        self, rules: tuple[RuleId, ...], check: _Check, path: str, model: Model, **context: Any
    ) -> None:
        if self.complete and "RG01" in self.counts:
            if check.status != "incumple" or check.code in {"missing_model", "missing_element"}:
                rules = ("RG01", *rules)
            else:
                self.record(("RG01",), _Check(
                    "no_evaluable", "unresolved_reference", "No hay un destino inequívoco para evaluar su existencia."
                ), path, model, **context)
        self.record(rules, check, path, model, **context)

    def _internal(self, model: Model, path: str, reference: Reference) -> Optional[Record]:
        value = reference.value
        check, target = _lookup(
            model.elements_by_id, value, self.declared_complete and not model.element_gaps, "element"
        )
        is_parent = reference.field == "parentId"
        self._element_reference(
            ("RG02",), check, path, model,
            element=reference.owner if is_parent else None,
            relationship=None if is_parent else reference.owner,
            evidence={"targetModelId": model.record.id, "targetElementId": identifier(value)},
        )
        return target

    def _origin(self, model: Model, path: str, reference: Reference) -> None:
        element = reference.owner if reference.field == "sourceModelElements" else None
        link = reference.value
        rules: tuple[RuleId, ...] = ("RG02", "RG05")
        report = self.record if element is None else self._element_reference
        if reference.unsupported_collection or (element is not None and (
            not isinstance(link, dict) or link.keys() - {"modelId", "elementId"}
        )):
            report(rules, _UNSUPPORTED_LINK, path, model, element=element)
            return

        model_id = link if element is None else link.get("modelId", MISSING)
        element_id = MISSING if element is None else link.get("elementId", MISSING)
        evidence = {"targetModelId": identifier(model_id), "targetElementId": identifier(element_id)}
        self._check_origin(model, element, path, evidence)

        if element is not None:
            invalid_fields = [
                (field, check) for field, value in (("modelId", model_id), ("elementId", element_id))
                if (check := _check_identifier(value)).status != "cumple"
            ]
            if invalid_fields:
                for field, check in invalid_fields:
                    report(rules, check, f"{path}/{field}", model, element=element, evidence=evidence)
                return

        scope_complete = self.declared_complete and not self.snapshot.inventory_gaps
        check, target_model = _lookup(self.snapshot.models_by_id, model_id, scope_complete, "model")
        if target_model is model:
            check = _Check("incumple", "same_model_origin", "El enlace de origen debe apuntar a otro modelo.")
        elif target_model is not None and element is not None:
            check, _ = _lookup(target_model.elements_by_id, element_id,
                               scope_complete and not target_model.element_gaps, "element")

        report(rules, check, path, model, element=element, evidence=evidence)

    def _check_origin(
        self, model: Model, element: Optional[Record], path: str, evidence: dict[str, Any],
    ) -> None:
        model_id = model.record.id
        if model_id is None or (element is not None and element.id is None):
            check = _Check("no_evaluable", "unidentified_origin", "Falta un ID válido en el extremo de origen.")
        elif len(self.snapshot.models_by_id[model_id]) > 1 or (
            element is not None and len(model.elements_by_id[element.id]) > 1
        ):
            check = _Check("incumple", "ambiguous_origin", "El identificador del origen es ambiguo.")
        else:
            return
        self.record(("RG05",), check, path, model, element=element, evidence=evidence)

    def _relationship_type(
        self, model: Model, relationship: Record, syntax: Optional[dict[str, Any]],
        source: Optional[Record], target: Optional[Record],
    ) -> None:
        relation_type = identifier(relationship.data.get("type"))
        language_id = _language_key(model.record.data.get("languageId"))
        evidence: dict[str, Any] = {"languageId": language_id, "relationshipType": relation_type}
        if syntax is None or relation_type is None:
            check = _Check("no_evaluable", "missing_relationship_context", "Falta el metamodelo o el tipo de relación.")
        elif relation_type not in syntax["relationships"]:
            check = _Check("incumple", "unknown_relationship_type", "El metamodelo no declara el tipo de relación.")
        else:
            definition = syntax["relationships"][relation_type]
            check = _compare_endpoints(syntax["elements"], definition, source, target, evidence)
        self.record(("RG04",), check, relationship.path, model, relationship=relationship, evidence=evidence)


def _compare_endpoints(
    element_types: dict[str, Any], definition: Any, source: Optional[Record],
    target: Optional[Record], evidence: dict[str, Any],
) -> _Check:
    allowed_source = definition.get("source") if isinstance(definition, dict) else None
    allowed_targets = definition.get("target") if isinstance(definition, dict) else None
    if (identifier(allowed_source) not in element_types or not isinstance(allowed_targets, list)
            or not allowed_targets or any(identifier(value) not in element_types for value in allowed_targets)):
        return _Check("no_evaluable", "incomplete_relationship_definition", "Falta una definición válida de los extremos.")

    source_type = identifier(source.data.get("type")) if source is not None else None
    target_type = identifier(target.data.get("type")) if target is not None else None
    if source_type is None or target_type is None:
        return _Check("no_evaluable", "unresolved_relationship_endpoints", "Faltan extremos resolubles con tipos explícitos.")
    evidence.update({
        "sourceId": source.id, "targetId": target.id,
        "sourceType": source_type, "targetType": target_type,
        "allowedSourceType": allowed_source, "allowedTargetTypes": list(allowed_targets),
    })
    if source_type != allowed_source or target_type not in allowed_targets:
        return _Check("incumple", "incompatible_endpoint_types", "Los tipos de los extremos no son compatibles.")
    return _PASS


def validate_snapshot(request: ValidationRequest) -> ValidationResult:
    """Evaluate the version 1 contract using only the supplied snapshot."""
    if not isinstance(request, dict) or type(request.get("schemaVersion")) is not int or request["schemaVersion"] != 1:
        raise ValueError("schemaVersion debe ser 1.")
    if identifier(request.get("projectId")) is None or identifier(request.get("snapshotId")) is None:
        raise ValueError("projectId y snapshotId deben ser identificadores de texto válidos.")
    scope = request.get("scope")
    if scope not in ("complete", "partial") or not isinstance(request.get("project"), dict):
        raise ValueError("Se requiere un proyecto JSON y un alcance complete o partial.")
    requested = request.get("ruleIds", list(_RULES))
    if not isinstance(requested, list) or any(not isinstance(rule, str) or rule not in _RULES for rule in requested):
        raise ValueError("ruleIds solo admite RG01 a RG05.")
    metamodels = request.get("metamodels", {})
    if not isinstance(metamodels, dict) or any(identifier(key) is None for key in metamodels):
        raise ValueError("metamodels debe indexarse por ID de lenguaje como texto.")

    snapshot = Snapshot.from_project(request["project"])
    evaluation = _Evaluation(snapshot, [rule for rule in _RULES if rule in requested], scope == "complete")
    evaluation.evaluate(metamodels)
    return {
        "schemaVersion": 1, "projectId": request["projectId"], "snapshotId": request["snapshotId"],
        "coverage": {
            "requestedScope": scope,
            "effectiveScope": "complete" if evaluation.complete else "partial",
            "models": [{"modelId": model.record.id, "path": model.record.path} for model in snapshot.models],
            "incompletePaths": evaluation.incomplete_paths,
        },
        "rules": evaluation.rule_results(),
        "findings": evaluation.findings,
    }

"""RG06-RG08 over explicit criteria, without semantic guesses or side effects."""

import argparse
import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from .contracts import Status
from .cli_input import read_local_capture
from .correspondence_contracts import CorrespondenceRequest, CorrespondenceResult
from .correspondence_profile import validate_profile
from .snapshot import MISSING, Model, Record, Snapshot, identifier


_RULES = {
    "RG06": "Compatibilidad de atributos de correspondencias según criterios explícitos.",
    "RG07": "Integridad y condiciones declaradas de dependencias entre modelos.",
    "RG08": "Cobertura de correspondencias requeridas y cardinalidades explícitas.",
}
_SEVERITY = {"cumple": "info", "incumple": "error", "no_evaluable": "warning", "no_aplica": "info"}


class CorrespondenceLimitExceeded(ValueError):
    pass


def _equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _attribute(record: Record, descriptor: dict[str, Any]) -> Any:
    if "property" in descriptor:
        properties = record.data.get("properties")
        if not isinstance(properties, list) or any(not isinstance(value, dict) or identifier(value.get("name")) is None for value in properties):
            return MISSING
        matches = [value for value in properties if isinstance(value, dict) and value.get("name") == descriptor["property"]]
        return matches[0].get("value", MISSING) if len(matches) == 1 else MISSING
    value: Any = record.data
    for field in descriptor["path"]:
        if not isinstance(value, dict) or field not in value:
            return MISSING
        value = value[field]
    return value


def _compare(left: Any, right: Any, operator: str) -> Optional[bool]:
    if left is MISSING or right is MISSING:
        return None
    equal = _equal(left, right)
    return equal if operator == "equals" else not equal


def _select(model: Model, element: Optional[Record], selector: dict[str, Any]) -> Optional[bool]:
    states = []
    for field, expected in selector.items():
        actual = (element.data.get("type") if element is not None else None) if field == "elementType" else (
            model.record.id if field == "modelId" else model.record.data.get("languageId")
        )
        if field == "languageId" and type(actual) is int:
            actual = str(actual)
        states.append(None if identifier(actual) is None else actual == (str(expected) if type(expected) is int else expected))
    return False if False in states else None if None in states else True


class _Evaluation:
    def __init__(self, request: CorrespondenceRequest, max_checks: Optional[int]):
        self.request = request
        self.snapshot = Snapshot.from_project(request["project"])
        self.complete = request["scope"] == "complete" and not self.snapshot.incomplete_paths
        self.limit = max_checks
        self.work = 0
        self.profile_id: Optional[str] = None
        self.counts = {rule: Counter() for rule in _RULES}
        self.paths = {rule: {} for rule in _RULES}
        self.findings = []

    def tick(self) -> None:
        self.work += 1
        if self.limit is not None and self.work > self.limit:
            raise CorrespondenceLimitExceeded("El análisis de correspondencias supera su límite de trabajo.")

    def record(self, rule: str, status: Status, code: str, description: str,
               model: Optional[Model] = None, element: Optional[Record] = None,
               path: str = "/project", evidence: Optional[dict[str, Any]] = None) -> None:
        self.counts[rule][status] += 1
        if model is not None:
            self.paths[rule][model.record.path] = None
        if status != "cumple":
            self.findings.append({
                "ruleIds": [rule], "status": status, "severity": _SEVERITY[status], "code": code,
                "description": description,
                "location": {"path": path, "modelId": model.record.id if model else None,
                             "elementId": element.id if element else None, "referenceId": None},
                "evidence": copy.deepcopy(evidence or {}),
            })

    def unavailable(self, code: str, description: str) -> CorrespondenceResult:
        # Discard partial success if evaluation could not finish.
        self.counts = {rule: Counter() for rule in _RULES}
        self.paths = {rule: {} for rule in _RULES}
        self.findings = []
        for rule in _RULES:
            self.record(rule, "no_evaluable", code, description)
        return self.result()

    def result(self) -> CorrespondenceResult:
        rules = []
        for rule, description in _RULES.items():
            status = next((value for value in ("incumple", "no_evaluable", "cumple", "no_aplica") if self.counts[rule][value]), "no_aplica")
            rules.append({"ruleId": rule, "status": status, "severity": _SEVERITY[status], "description": description,
                          "checkCounts": dict(self.counts[rule]), "modelPaths": list(self.paths[rule])})
        return {
            "schemaVersion": 1, "kind": "correspondences", "projectId": self.request["projectId"],
            "snapshotId": self.request["snapshotId"], "profileId": self.profile_id,
            "coverage": {"requestedScope": self.request["scope"], "effectiveScope": "complete" if self.complete else "partial",
                         "models": [{"modelId": model.record.id, "path": model.record.path} for model in self.snapshot.models],
                         "incompletePaths": self.snapshot.incomplete_paths},
            "rules": rules, "findings": self.findings,
        }

    def resolve(self, model_id: Any, element_id: Any = MISSING) -> tuple[Status, Optional[Model], Optional[Record]]:
        if identifier(model_id) is None or (element_id is not MISSING and identifier(element_id) is None):
            return "no_evaluable", None, None
        matches = self.snapshot.models_by_id.get(model_id, [])
        if len(matches) != 1:
            return ("incumple" if not matches and self.complete else "no_evaluable"), None, None
        model = matches[0]
        if element_id is MISSING:
            return "cumple", model, None
        elements = model.elements_by_id.get(element_id, [])
        if len(elements) != 1:
            return ("incumple" if not elements and self.complete and not model.element_gaps else "no_evaluable"), model, None
        return "cumple", model, elements[0]

    def links(self, model: Model, owner: Record, level: str, target_selector: Optional[dict[str, Any]] = None):
        field = "sourceModelIds" if level == "model" else "sourceModelElements"
        values = owner.data.get(field, MISSING)
        if not isinstance(values, list):
            yield None, None, f"{owner.path}/{field}", "no_evaluable"
            return
        for index, value in enumerate(values):
            self.tick()
            path = f"{owner.path}/{field}/{index}"
            if level == "model":
                model_id, element_id = value, MISSING
            elif isinstance(value, dict) and set(value) == {"modelId", "elementId"}:
                model_id, element_id = value["modelId"], value["elementId"]
            else:
                yield None, None, path, "no_evaluable"
                continue
            if (target_selector is not None and "modelId" in target_selector and identifier(model_id) is not None
                    and model_id != target_selector["modelId"]):
                continue
            status, target_model, target = self.resolve(model_id, element_id)
            if status == "cumple" and target_model is model:
                status = "incumple"
            yield target_model, target, path, status

    def correspondences(self, policies: list[dict[str, Any]]) -> None:
        for policy in policies:
            level = policy.get("level", "element")
            for model in self.snapshot.models:
                self.tick()
                owners = [model.record] if level == "model" else model.elements
                for owner in owners:
                    self.tick()
                    element = owner if level == "element" else None
                    selected = _select(model, element, policy["source"])
                    if selected is False:
                        continue
                    evidence = {"policyId": policy["id"]}
                    source_status, _, _ = self.resolve(model.record.id, owner.id if element is not None else MISSING)
                    if selected is None or source_status != "cumple":
                        self.record("RG06", "no_evaluable", "unresolved_source", "No hay un origen inequívoco para aplicar el criterio.", model, element, owner.path, evidence)
                        continue
                    for target_model, target, path, status in self.links(model, owner, level, policy["target"]):
                        if status != "cumple":
                            self.record("RG06", "no_evaluable", "unresolved_correspondence", "La correspondencia no permite comparar ambos extremos.", model, element, path, evidence)
                            continue
                        target_selected = _select(target_model, target, policy["target"])
                        if target_selected is False:
                            continue
                        if target_selected is None:
                            self.record("RG06", "no_evaluable", "unknown_target_context", "Falta contexto del destino para seleccionar el criterio.", model, element, path, evidence)
                            continue
                        for check in policy["checks"]:
                            self.tick()
                            result = _compare(_attribute(owner, check["source"]), _attribute(target if target is not None else target_model.record, check["target"]), check["operator"])
                            state = "no_evaluable" if result is None else "cumple" if result else "incumple"
                            self.record("RG06", state, "missing_comparison_data" if result is None else "attribute_mismatch",
                                        "Faltan atributos únicos para comparar." if result is None else "Los atributos no satisfacen la correspondencia declarada.",
                                        model, element, path, {**evidence, "targetModelId": target_model.record.id,
                                        "targetElementId": target.id if target else None, "sourceAttribute": check["source"], "targetAttribute": check["target"]})

    def dependencies(self, policies: list[dict[str, Any]]) -> None:
        for policy in policies:
            self.tick()
            source = policy["source"]
            status, model, element = self.resolve(source["modelId"], source["elementId"])
            evidence = {"policyId": policy["id"], "targetModelId": policy["target"]["modelId"], "targetElementId": policy["target"]["elementId"]}
            if status != "cumple":
                self.record("RG07", status, "unresolved_dependency_source", "El origen de la dependencia no puede resolverse.", model, element, evidence=evidence)
                continue
            guards = []
            for guard in policy.get("when", []):
                self.tick()
                guards.append(_compare(_attribute(element, guard["attribute"]), guard["value"], guard["operator"]))
            if False in guards:
                self.record("RG07", "no_aplica", "inactive_dependency", "La condición de activación no se cumple.", model, element, element.path, evidence)
                continue
            if None in guards:
                self.record("RG07", "no_evaluable", "unknown_dependency_activation", "Faltan datos para determinar si se activa la dependencia.", model, element, element.path, evidence)
                continue
            status, target_model, target = self.resolve(policy["target"]["modelId"], policy["target"]["elementId"])
            if status != "cumple":
                self.record("RG07", status, "unresolved_dependency_target", "El destino de la dependencia no puede resolverse.", model, element, element.path, evidence)
                continue
            if policy.get("requireOriginLink", False):
                states = list(self.links(model, element, "element"))
                linked = any(state == "cumple" and destination is target and destination_model is target_model
                             for destination_model, destination, _, state in states)
                uncertain = any(state == "no_evaluable" for _, _, _, state in states)
                state = "cumple" if linked else "no_evaluable" if uncertain or not self.complete else "incumple"
                self.record("RG07", state, "missing_dependency_link", "La dependencia exige una correspondencia de origen explícita.", model, element, element.path, evidence)
            for condition in policy["conditions"]:
                self.tick()
                owner = element if condition["on"] == "source" else target
                result = _compare(_attribute(owner, condition["attribute"]), condition["value"], condition["operator"])
                state = "no_evaluable" if result is None else "cumple" if result else "incumple"
                self.record("RG07", state, "unknown_dependency_condition" if result is None else "incompatible_dependency_condition",
                            "Faltan datos para evaluar la condición." if result is None else "La dependencia no respeta una condición explícita.", model, element, element.path,
                            {**evidence, "conditionSide": condition["on"], "attribute": condition["attribute"]})
            if not policy["conditions"] and not policy.get("requireOriginLink", False):
                self.record("RG07", "cumple", "resolved_dependency", "Los extremos de la dependencia existen.", model, element, element.path, evidence)

    def required(self, policies: list[dict[str, Any]]) -> None:
        for policy in policies:
            for model in self.snapshot.models:
                self.tick()
                for element in model.elements:
                    self.tick()
                    selected = _select(model, element, policy["source"])
                    if selected is False:
                        continue
                    evidence = {"policyId": policy["id"], "minimum": policy.get("minimum", 1), "maximum": policy.get("maximum")}
                    status, _, _ = self.resolve(model.record.id, element.id)
                    if selected is None or status != "cumple":
                        self.record("RG08", "no_evaluable", "unknown_required_source", "Falta un origen inequívoco para comprobar sus correspondencias.", model, element, element.path, evidence)
                        continue
                    targets = set()
                    unknown = False
                    for target_model, target, _, state in self.links(model, element, "element", policy["target"]):
                        if state == "no_evaluable":
                            unknown = True
                        elif state == "cumple":
                            match = _select(target_model, target, policy["target"])
                            if match is None:
                                unknown = True
                            elif match:
                                targets.add((target_model.record.id, target.id))
                    count = len(targets)
                    minimum, maximum = policy.get("minimum", 1), policy.get("maximum")
                    if maximum is not None and count > maximum:
                        state = "incumple"
                    elif count < minimum:
                        state = "no_evaluable" if unknown or not self.complete else "incumple"
                    else:
                        state = "no_evaluable" if unknown and maximum is not None else "cumple"
                    self.record("RG08", state, "orphan_element" if count < minimum else "excess_correspondences",
                                "No se alcanza la cobertura de correspondencias requerida." if count < minimum else "La cardinalidad de correspondencias requiere revisión.",
                                model, element, element.path, {**evidence, "distinctTargets": count})


def validate_correspondences(request: CorrespondenceRequest, *, max_checks: Optional[int] = None) -> CorrespondenceResult:
    if not isinstance(request, dict) or type(request.get("schemaVersion")) is not int or request["schemaVersion"] != 1:
        raise ValueError("schemaVersion debe ser 1.")
    if identifier(request.get("projectId")) is None or identifier(request.get("snapshotId")) is None:
        raise ValueError("projectId y snapshotId deben ser identificadores válidos.")
    if request.get("scope") not in ("complete", "partial") or not isinstance(request.get("project"), dict):
        raise ValueError("Se requiere proyecto JSON y alcance complete o partial.")
    if "id" in request["project"] and request["project"]["id"] != request["projectId"]:
        raise ValueError("project.id debe coincidir con projectId.")
    if max_checks is not None and (type(max_checks) is not int or max_checks < 0):
        raise ValueError("max_checks debe ser un entero no negativo.")
    evaluation = _Evaluation(request, max_checks)
    if "correspondenceProfile" not in request:
        return evaluation.unavailable("missing_correspondence_profile", "Faltan criterios explícitos para evaluar RG06 a RG08.")
    try:
        profile = validate_profile(request["correspondenceProfile"])
    except (ValueError, RecursionError) as error:
        return evaluation.unavailable("invalid_correspondence_profile", f"Los criterios no son evaluables. {error}")
    evaluation.profile_id = profile["id"]
    if not evaluation.complete:
        for rule, field in (("RG06", "correspondences"), ("RG07", "dependencies"), ("RG08", "requiredCorrespondences")):
            if profile[field]:
                evaluation.record(rule, "no_evaluable", "incomplete_correspondence_capture", "La cobertura parcial impide descartar elementos o referencias fuera de la captura.")
    try:
        evaluation.correspondences(profile["correspondences"])
        evaluation.dependencies(profile["dependencies"])
        evaluation.required(profile["requiredCorrespondences"])
    except CorrespondenceLimitExceeded:
        return evaluation.unavailable("correspondence_evaluation_limit", "El análisis de correspondencias supera el límite de trabajo; no se presenta una evaluación parcial como completa.")
    return evaluation.result()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalúa RG06 a RG08 con criterios explícitos sobre una captura local.")
    parser.add_argument("snapshot", type=Path, help="Archivo JSON dentro del directorio de trabajo.")
    args = parser.parse_args()
    try:
        report = validate_correspondences(read_local_capture(args.snapshot))
    except (OSError, ValueError, RecursionError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

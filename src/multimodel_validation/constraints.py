"""Bounded Boolean consistency over explicit bindings, without native-language guesses."""

import argparse
import copy
import json
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from typing import Any, Optional

from .constraint_contracts import ConstraintRequest, ConstraintResult
from .cli_input import read_local_capture
from .constraint_profile import validate_profile, variables_in
from .contracts import Status
from .correspondence import _attribute, _equal
from .snapshot import MISSING, Snapshot, identifier


_RULES = {
    "RG09": "Satisfacibilidad conjunta de restricciones explícitas entre modelos.",
    "RG10": "Compatibilidad lógica y estado observado de dependencias explícitas.",
    "RG11": "Compatibilidad de condiciones explícitas de un mismo concepto.",
    "RG12": "Contradicciones observadas en proposiciones vinculadas entre modelos.",
}
_SEVERITY = {"cumple": "info", "incumple": "error", "no_evaluable": "warning", "no_aplica": "info"}


class ConstraintLimitExceeded(ValueError):
    pass


class _Evaluation:
    def __init__(self, request: ConstraintRequest, max_checks: Optional[int]):
        self.request = request
        self.snapshot = Snapshot.from_project(request["project"])
        self.complete = request["scope"] == "complete" and not self.snapshot.incomplete_paths
        self.limit = max_checks
        self.work = 0
        self.profile_id: Optional[str] = None
        self.counts = {rule: Counter() for rule in _RULES}
        self.paths = {rule: {} for rule in _RULES}
        self.findings = []
        self.bindings: dict[str, list[tuple[dict[str, Any], Optional[bool]]]] = {}

    def tick(self) -> None:
        self.work += 1
        if self.limit is not None and self.work > self.limit:
            raise ConstraintLimitExceeded()

    def record(self, rule: str, status: Status, code: str, description: str,
               evidence: Optional[dict[str, Any]] = None, model_ids: tuple[str, ...] = ()) -> None:
        self.counts[rule][status] += 1
        for model_id in model_ids:
            for model in self.snapshot.models_by_id.get(model_id, []):
                self.paths[rule][model.record.path] = None
        if status != "cumple":
            self.findings.append({
                "ruleIds": [rule], "status": status, "severity": _SEVERITY[status], "code": code,
                "description": description,
                "location": {"path": "/constraintProfile", "modelId": model_ids[0] if model_ids else None,
                             "elementId": None, "referenceId": None},
                "evidence": copy.deepcopy(evidence or {}),
            })

    def result(self) -> ConstraintResult:
        rules = []
        for rule, description in _RULES.items():
            status = next((s for s in ("incumple", "no_evaluable", "cumple") if self.counts[rule][s]), "no_aplica")
            rules.append({"ruleId": rule, "status": status, "severity": _SEVERITY[status],
                          "description": description, "checkCounts": dict(self.counts[rule]), "modelPaths": list(self.paths[rule])})
        return {
            "schemaVersion": 1, "kind": "constraints", "projectId": self.request["projectId"],
            "snapshotId": self.request["snapshotId"], "profileId": self.profile_id,
            "coverage": {"requestedScope": self.request["scope"], "effectiveScope": "complete" if self.complete else "partial",
                         "models": [{"modelId": m.record.id, "path": m.record.path} for m in self.snapshot.models],
                         "incompletePaths": self.snapshot.incomplete_paths},
            "rules": rules, "findings": self.findings,
        }

    def unavailable(self, code: str, description: str) -> ConstraintResult:
        self.counts = {rule: Counter() for rule in _RULES}
        self.paths = {rule: {} for rule in _RULES}
        self.findings = []
        for rule in _RULES:
            self.record(rule, "no_evaluable", code, description)
        return self.result()

    def model_exists(self, model_id: str) -> bool:
        return len(self.snapshot.models_by_id.get(model_id, [])) == 1

    def read_binding(self, binding: dict[str, Any]) -> Optional[bool]:
        self.tick()
        matches = self.snapshot.models_by_id.get(binding["modelId"], [])
        if len(matches) != 1:
            return None
        model = matches[0]
        record = model.record
        if "elementId" in binding:
            elements = model.elements_by_id.get(binding["elementId"], [])
            if len(elements) != 1:
                return None
            record = elements[0]
        value = _attribute(record, binding["attribute"])
        if value is MISSING:
            return None
        if _equal(value, binding.get("trueValue", True)):
            return True
        if _equal(value, binding.get("falseValue", False)):
            return False
        return None

    def evaluate(self, expression: Any, values: dict[str, Optional[bool]]) -> Optional[bool]:
        self.tick()
        if type(expression) is bool:
            return expression
        operator, operand = next(iter(expression.items()))
        if operator == "var":
            return values.get(operand)
        if operator == "not":
            value = self.evaluate(operand, values)
            return None if value is None else not value
        states = [self.evaluate(child, values) for child in operand]
        if operator == "and":
            return False if False in states else None if None in states else True
        if operator == "or":
            return True if True in states else None if None in states else False
        left, right = states
        return True if left is False or right is True else None if None in states else False

    def ready(self, expression: Any) -> bool:
        return all(self.bindings[name] and all(value is not None for _, value in self.bindings[name])
                   for name in variables_in(expression))

    def observed(self, expression: Any, model_id: str) -> Optional[bool]:
        values = {}
        for name in variables_in(expression):
            states = [value for binding, value in self.bindings[name] if binding["modelId"] == model_id]
            values[name] = states[0] if states and None not in states and len(set(states)) == 1 else None
        return self.evaluate(expression, values)

    def satisfiable(self, expressions: list[Any]) -> bool:
        names = sorted(set().union(*(variables_in(expr) for expr in expressions)))
        for states in product((False, True), repeat=len(names)):
            self.tick()
            assignment = dict(zip(names, states))
            if all(self.evaluate(expr, assignment) is True for expr in expressions):
                return True
        return False

    def logical_group(self, rule: str, entries: list[dict[str, Any]]) -> None:
        if len({entry["modelId"] for entry in entries}) < 2:
            return
        valid = []
        for entry in entries:
            self.tick()
            if not self.model_exists(entry["modelId"]) or not self.ready(entry["expression"]):
                self.record(rule, "no_evaluable", "unresolved_constraint_binding",
                            "Falta un modelo, elemento o atributo inequívoco para interpretar el criterio.",
                            {"criterionIds": [entry["id"]]}, (entry["modelId"],))
            else:
                valid.append(entry)
        if len({entry["modelId"] for entry in valid}) < 2:
            return
        compatible = self.satisfiable([entry["expression"] for entry in valid])
        self.record(rule, "cumple" if compatible else "incumple",
                    "compatible_constraints" if compatible else "contradictory_constraints",
                    "Las expresiones declaradas no admiten ninguna asignación booleana común.",
                    {"criterionIds": [entry["id"] for entry in valid], "conceptIds": sorted({entry["conceptId"] for entry in valid if "conceptId" in entry})},
                    tuple(dict.fromkeys(entry["modelId"] for entry in valid)))

    def dependencies(self, entries: list[dict[str, Any]], constraints: list[dict[str, Any]]) -> None:
        for entry in entries:
            self.tick()
            source, target = entry["sourceModelId"], entry["targetModelId"]
            evidence = {"criterionIds": [entry["id"]], "sourceModelId": source, "targetModelId": target}
            active = self.observed(entry["when"], source) if self.model_exists(source) else None
            if active is False:
                self.record("RG10", "no_aplica", "inactive_constraint_dependency", "La condición de activación es falsa.", evidence, (source, target))
                continue
            context_ready = all(self.model_exists(c["modelId"]) and self.ready(c["expression"]) for c in constraints)
            if not all(self.model_exists(m) for m in (source, target)) or not context_ready or not all(self.ready(entry[f]) for f in ("when", "requires")):
                self.record("RG10", "no_evaluable", "unresolved_constraint_dependency", "Faltan datos para evaluar la dependencia y su contexto lógico.", evidence, (source, target))
                continue
            context = [c["expression"] for c in constraints]
            if not self.satisfiable(context):
                self.record("RG10", "no_evaluable", "inconsistent_constraint_context", "El contexto ya es contradictorio; no se atribuye ese fallo a la dependencia.", evidence, (source, target))
                continue
            if not self.satisfiable(context + [entry["when"]]):
                if active is True:
                    status, code, message = "incumple", "forbidden_constraint_activation", "La dependencia está activa en la captura, pero las restricciones impiden su activación."
                else:
                    status, code, message = "no_aplica", "unreachable_constraint_dependency", "Las restricciones impiden activar esta dependencia."
            elif not self.satisfiable(context + [entry["when"], entry["requires"]]):
                status, code, message = "incumple", "incompatible_constraint_dependency", "El requisito de la dependencia es incompatible con las restricciones cuando se activa."
            else:
                required = self.observed(entry["requires"], target)
                if active is None or required is None:
                    status, code, message = "no_evaluable", "unknown_dependency_condition", "No se puede determinar la activación o el requisito en sus modelos declarados."
                elif required:
                    status, code, message = "cumple", "compatible_constraint_dependency", "La dependencia activa es compatible y su requisito observado se satisface."
                else:
                    status, code, message = "incumple", "unsatisfied_constraint_dependency", "La dependencia está activa y su requisito no se satisface en el destino."
            self.record("RG10", status, code, message, evidence, (source, target))

    def assertions(self) -> None:
        for name, bindings in self.bindings.items():
            self.tick()
            model_ids = tuple(dict.fromkeys(binding["modelId"] for binding, _ in bindings))
            if len(model_ids) < 2:
                continue
            states = [value for _, value in bindings]
            observed = [(binding["modelId"], value) for binding, value in bindings if value is not None]
            conflict = any(left_model != right_model and left != right
                           for left_model, left in observed for right_model, right in observed)
            if conflict:
                status, code, message = "incumple", "contradictory_assertions", "Los modelos afirman valores booleanos opuestos para la misma proposición declarada."
            elif None in states:
                status, code, message = "no_evaluable", "unknown_assertion", "Falta un valor inequívoco para comparar las afirmaciones declaradas."
            else:
                status, code, message = "cumple", "compatible_assertions", "Las afirmaciones observadas coinciden según la correspondencia explícita."
            self.record("RG12", status, code, message, {"variableIds": [name], "bindings": [
                {"modelId": b["modelId"], **({"elementId": b["elementId"]} if "elementId" in b else {})} for b, _ in bindings
            ]}, model_ids)


def validate_constraints(request: ConstraintRequest, *, max_checks: Optional[int] = 10_000) -> ConstraintResult:
    if not isinstance(request, dict) or type(request.get("schemaVersion")) is not int or request["schemaVersion"] != 1:
        raise ValueError("schemaVersion debe ser 1.")
    if identifier(request.get("projectId")) is None or identifier(request.get("snapshotId")) is None:
        raise ValueError("Se requieren projectId y snapshotId válidos.")
    if request.get("scope") not in ("complete", "partial") or not isinstance(request.get("project"), dict):
        raise ValueError("Se requiere proyecto JSON y alcance complete o partial.")
    if "id" in request["project"] and request["project"]["id"] != request["projectId"]:
        raise ValueError("project.id debe coincidir con projectId.")
    if max_checks is not None and (type(max_checks) is not int or max_checks < 0):
        raise ValueError("max_checks debe ser un entero no negativo.")
    evaluation = _Evaluation(request, max_checks)
    if "constraintProfile" not in request:
        return evaluation.unavailable("missing_constraint_profile", "Faltan criterios explícitos para evaluar RG09 a RG12.")
    try:
        profile = validate_profile(request["constraintProfile"])
    except (ValueError, RecursionError) as error:
        return evaluation.unavailable("invalid_constraint_profile", f"Los criterios de restricciones no son evaluables. {error}")
    evaluation.profile_id = profile["id"]
    try:
        evaluation.bindings = {v["id"]: [(b, evaluation.read_binding(b)) for b in v["bindings"]] for v in profile["variables"]}
        if not evaluation.complete:
            for rule, active in (("RG09", profile["constraints"]), ("RG10", profile["dependencies"]),
                                 ("RG11", profile["conditions"]), ("RG12", [v for v in profile["variables"] if len({b["modelId"] for b in v["bindings"]}) > 1])):
                if active:
                    evaluation.record(rule, "no_evaluable", "incomplete_constraint_capture", "La captura parcial impide certificar la comprobación completa de los criterios.")
        evaluation.logical_group("RG09", profile["constraints"])
        evaluation.dependencies(profile["dependencies"], profile["constraints"])
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for condition in profile["conditions"]:
            groups[condition["conceptId"]].append(condition)
        for entries in groups.values():
            evaluation.logical_group("RG11", entries)
        evaluation.assertions()
    except ConstraintLimitExceeded:
        return evaluation.unavailable("constraint_evaluation_limit", "Se agotó el presupuesto de evaluación; no se presenta un análisis parcial como completo.")
    return evaluation.result()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalúa RG09 a RG12 mediante criterios booleanos explícitos.")
    parser.add_argument("snapshot", type=Path, help="Archivo JSON dentro del directorio de trabajo.")
    args = parser.parse_args()
    try:
        report = validate_constraints(read_local_capture(args.snapshot))
    except (OSError, ValueError, RecursionError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

"""Read-only RG13-RG16 analysis of explicit structural dependencies.

No HTTP registration, persistence, repair, historical reconstruction or LLM use.
The existing snapshot validator and its public contract are unchanged.
"""

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .contracts import Coverage, Finding, Severity, Status, ValidationResult
from .cli_input import read_local_capture
from .correspondence import validate_correspondences
from .constraints import validate_constraints
from .versions import validate_versions
from .traceability import validate_traceability
from .snapshot import Record, Snapshot, identifier
from .validator import validate_snapshot
from .transition_contracts import (
    Change, CorrespondenceReview, EntityAddress, EntityKind, Impact, Phase, StructuralDelta, TransitionCapture,
    TransitionFinding, TransitionRequest, TransitionResult, TransitionRuleId,
)


_FIELDS = ["sourceId", "targetId", "parentId", "sourceModelIds", "sourceModelElements"]
_RULES: dict[TransitionRuleId, str] = {
    "RG13": "Impacto potencial directo mediante referencias estructurales explícitas.",
    "RG14": "Revisión estructural de correspondencias afectadas y señalización de revisión semántica pendiente.",
    "RG15": "Ausencia de referencias supervivientes a modelos o elementos eliminados en esta transición.",
    "RG16": "Consistencia estructural posterior respecto de RG01 a RG05, con comparación del estado anterior.",
}
_STRUCTURAL_RULES = ["RG01", "RG02", "RG03", "RG04", "RG05"]
_ORIGIN_FIELDS = {"sourceModelIds", "sourceModelElements"}
# Canonical presentation fields of VariaMos records, not language-specific properties.
_PRESENTATION_FIELDS = {"x", "y", "width", "height", "points", "inconsistent", "consistencyError"}
_SEVERITY: dict[Status, Severity] = {
    "cumple": "info", "incumple": "error", "no_evaluable": "warning", "no_aplica": "info",
}
_Key = tuple[EntityKind, str, str]


class TransitionLimitExceeded(ValueError):
    """The caller's bound on report expansion was exceeded."""


def _address(key: _Key) -> EntityAddress:
    return {"kind": key[0], "modelId": key[1], "id": key[2]}


def _key(address: EntityAddress) -> _Key:
    return address["kind"], address["modelId"], address["id"]


def _capture(value: Any) -> TransitionCapture:
    if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError("Cada captura debe tener schemaVersion 1.")
    if identifier(value.get("projectId")) is None or identifier(value.get("snapshotId")) is None:
        raise ValueError("Cada captura requiere projectId y snapshotId válidos.")
    if value.get("scope") not in ("complete", "partial") or not isinstance(value.get("project"), dict):
        raise ValueError("Cada captura requiere un proyecto JSON y alcance complete o partial.")
    embedded_id = value["project"].get("id")
    if "id" in value["project"] and embedded_id != value["projectId"]:
        raise ValueError("project.id debe coincidir con projectId cuando esté presente.")
    return value


@dataclass(frozen=True)
class _Edge:
    owner: _Key
    target: _Key
    field: str
    path: str


class _State:
    def __init__(self, capture: TransitionCapture, phase: Phase):
        self.capture = capture
        self.phase = phase
        self.snapshot = Snapshot.from_project(capture["project"])
        self.entities: dict[_Key, list[Record]] = defaultdict(list)
        self.problems: list[tuple[str, str]] = []
        self.gaps = self.snapshot.incomplete_paths
        for model in self.snapshot.models:
            model_id = model.record.id
            if model_id is None:
                continue
            self.entities[("model", model_id, model_id)].append(model.record)
            if len(self.snapshot.models_by_id[model_id]) != 1:
                continue
            for kind, records in (("element", model.elements), ("relationship", model.relationships.values())):
                for record in records:
                    if record.id is None:
                        self.gaps.append(f"{record.path}/id")
                    else:
                        self.entities[(kind, model_id, record.id)].append(record)
        self.gaps = list(dict.fromkeys(self.gaps))
        for records in self.entities.values():
            if len(records) > 1:
                self.problems.append(("ambiguous_identity", records[0].path))
        self.edges = self._edges()

    @property
    def complete(self) -> bool:
        return self.capture["scope"] == "complete" and not self.gaps

    def coverage(self) -> Coverage:
        return {
            "requestedScope": self.capture["scope"],
            "effectiveScope": "complete" if self.complete else "partial",
            "models": [{"modelId": model.record.id, "path": model.record.path} for model in self.snapshot.models],
            "incompletePaths": list(self.gaps),
        }

    def unique(self, key: _Key) -> Optional[Record]:
        records = self.entities.get(key, [])
        return records[0] if len(records) == 1 else None

    def ambiguous(self, key: _Key) -> bool:
        return len(self.snapshot.models_by_id.get(key[1], [])) > 1 or len(self.entities.get(key, [])) > 1

    def _edges(self) -> list[_Edge]:
        edges = []
        owners = {record.path: key for key, records in self.entities.items()
                  if len(records) == 1 for record in records}
        for model in self.snapshot.models:
            model_id = model.record.id
            if model_id is None or len(self.snapshot.models_by_id[model_id]) != 1:
                continue
            for path, reference in model.reference_index().items():
                owner = owners.get(reference.owner.path)
                value = reference.value
                target = None
                if not reference.unsupported_collection:
                    if reference.field == "sourceModelIds" and identifier(value) is not None:
                        target = ("model", value, value)
                    elif reference.field == "sourceModelElements":
                        if (isinstance(value, dict) and set(value) == {"modelId", "elementId"}
                                and identifier(value["modelId"]) is not None
                                and identifier(value["elementId"]) is not None):
                            target = ("element", value["modelId"], value["elementId"])
                    elif identifier(value) is not None:
                        target = ("element", model_id, value)
                if owner is None or target is None:
                    self.problems.append(("unsupported_reference", path))
                elif not self.ambiguous(target):
                    edges.append(_Edge(owner, target, reference.field, path))
        return edges


def _fields(record: Record, kind: EntityKind) -> dict[str, Any]:
    return {key: value for key, value in record.data.items()
            if kind != "model" or key not in ("elements", "relationships")}


def _same_json(left: Any, right: Any) -> bool:
    # Python equates True with 1 and False with 0, unlike typed JSON data.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _certain_difference(left: Any, right: Any) -> bool:
    """Partial collections cannot prove a nested edit merely by omission/order."""
    if isinstance(left, list) or isinstance(right, list):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return any(_certain_difference(left[key], right[key]) for key in left.keys() & right.keys())
    return not _same_json(left, right)


def _changes(before: _State, after: _State) -> list[Change]:
    changes: list[Change] = []
    for key in sorted(before.entities.keys() | after.entities.keys()):
        if before.ambiguous(key) or after.ambiguous(key):
            continue
        # A model creation/deletion represents its entire subtree once.
        if key[0] != "model" and (not before.snapshot.models_by_id.get(key[1])
                                  or not after.snapshot.models_by_id.get(key[1])):
            continue
        previous, current = before.unique(key), after.unique(key)
        changed_fields: list[str] = []
        uncertain_fields: list[str] = []
        if previous is not None and current is not None:
            old, new = _fields(previous, key[0]), _fields(current, key[0])
            complete = before.complete and after.complete
            fields = old.keys() | new.keys() if complete else old.keys() & new.keys()
            for field in sorted(fields):
                if (field in old) == (field in new) and _same_json(old.get(field), new.get(field)):
                    continue
                destination = changed_fields if complete or _certain_difference(old.get(field), new.get(field)) else uncertain_fields
                destination.append(field)
            if not changed_fields and not uncertain_fields:
                continue
            operation = "updated"
        elif previous is not None and after.complete:
            operation = "deleted"
        elif current is not None and before.complete:
            operation = "created"
        else:
            continue
        changes.append({
            "operation": operation, "entity": _address(key),
            "beforePath": previous.path if previous is not None else None,
            "afterPath": current.path if current is not None else None,
            "changedFields": changed_fields,
            "uncertainFields": uncertain_fields,
        })
    return changes


def _incoming(changes: list[Change], states: tuple[_State, _State], max_impacts: Optional[int]) -> list[Impact]:
    # Inverse indices avoid scanning every reference for every changed entity.
    impacts: list[Impact] = []
    for state in states:
        by_target: dict[_Key, list[_Edge]] = defaultdict(list)
        by_model: dict[str, list[_Edge]] = defaultdict(list)
        for edge in state.edges:
            by_target[edge.target].append(edge)
            by_model[edge.target[1]].append(edge)
        for position, change in enumerate(changes):
            key = _key(change["entity"])
            if key[0] == "model" and change["operation"] in ("created", "deleted"):
                edges = by_model[key[1]]
            else:
                edges = list(by_target[key])
                if key[0] != "model":
                    edges.extend(by_target[("model", key[1], key[1])])
            for edge in edges:
                if max_impacts is not None and len(impacts) >= max_impacts:
                    raise TransitionLimitExceeded("La transición supera el límite de impactos admitido.")
                impacts.append({
                    "changeIndex": position, "phase": state.phase,
                    "dependent": _address(edge.owner), "target": _address(edge.target),
                    "field": edge.field, "referencePath": edge.path,
                })
    return impacts


def _structural_capture(capture: TransitionCapture) -> ValidationResult:
    # The transition caller cannot narrow the rules needed for RG16.
    return validate_snapshot({
        "schemaVersion": 1, "projectId": capture["projectId"], "snapshotId": capture["snapshotId"],
        "scope": capture["scope"], "project": capture["project"],
        "metamodels": capture.get("metamodels", {}), "ruleIds": list(_STRUCTURAL_RULES),
    })


def _finding_key(finding: Finding, state: _State) -> Optional[str]:
    location = finding["location"]
    model_id = location["modelId"]
    if model_id is None or len(state.snapshot.models_by_id.get(model_id, [])) != 1:
        return None
    kind, record_id = ("element", location["elementId"]) if location["elementId"] is not None else (
        ("relationship", location["referenceId"]) if location["referenceId"] is not None else ("model", model_id)
    )
    if state.unique((kind, model_id, record_id)) is None:
        return None
    path = location["path"]
    field = next((field for field in _FIELDS if f"/{field}" in path), "record")
    # Array positions and diagnostic paths are not stable entity identity.
    evidence = {name: value for name, value in finding["evidence"].items() if name in {
        "targetModelId", "targetElementId", "relationshipType", "sourceId", "targetId", "sourceType", "targetType",
    }}
    return json.dumps([sorted(finding["ruleIds"]), finding["code"], kind, model_id, record_id, field, evidence], sort_keys=True)


def _structural_delta(before: _State, after: _State, previous: ValidationResult, current: ValidationResult) -> StructuralDelta:
    remaining: Counter = Counter()
    before_full = before.complete and not before.problems and all(rule["status"] != "no_evaluable" for rule in previous["rules"])
    after_full = after.complete and not after.problems and all(rule["status"] != "no_evaluable" for rule in current["rules"])
    for finding in previous["findings"]:
        if finding["status"] == "incumple":
            key = _finding_key(finding, before)
            if key is not None:
                remaining[key] += 1
    delta: StructuralDelta = {"introduced": 0, "persisting": 0, "resolved": 0, "unclassified": 0}
    for finding in current["findings"]:
        if finding["status"] != "incumple":
            continue
        key = _finding_key(finding, after)
        if key is not None and remaining[key]:
            delta["persisting"] += 1
            remaining[key] -= 1
        elif key is not None and before_full:
            delta["introduced"] += 1
        else:
            delta["unclassified"] += 1
    if after_full:
        delta["resolved"] = sum(remaining.values())
    return delta


def _correspondence_reviews(before: _State, after: _State, changes: list[Change], impacts: list[Impact],
                            validation: ValidationResult) -> list[CorrespondenceReview]:
    origin_findings: dict[str, list[Finding]] = defaultdict(list)
    for finding in validation["findings"]:
        if "RG05" in finding["ruleIds"]:
            path = finding["location"]["path"]
            root = path.rsplit("/", 1)[0] if path.endswith(("/modelId", "/elementId")) else path
            origin_findings[root].append(finding)
    relevant: dict[_Key, list[int]] = defaultdict(list)
    model_context: dict[str, list[int]] = defaultdict(list)
    model_children: dict[str, list[int]] = defaultdict(list)
    for index, change in enumerate(changes):
        fields = set(change["changedFields"]) | set(change["uncertainFields"])
        if change["operation"] == "updated" and not fields - _PRESENTATION_FIELDS - _ORIGIN_FIELDS:
            continue
        key = _key(change["entity"])
        relevant[key].append(index)
        model_children[key[1]].append(index)
        if key[0] == "model":
            model_context[key[1]].append(index)
    incoming: dict[str, list[int]] = defaultdict(list)
    for impact in impacts:
        if impact["phase"] == "after" and impact["field"] in _ORIGIN_FIELDS:
            change = changes[impact["changeIndex"]]
            if _key(change["entity"]) in relevant:
                incoming[impact["referencePath"]].append(impact["changeIndex"])
    old_links = {(edge.owner, edge.target, edge.field) for edge in before.edges if edge.field in _ORIGIN_FIELDS}
    new_links = {(edge.owner, edge.target, edge.field) for edge in after.edges if edge.field in _ORIGIN_FIELDS}
    reviews: list[CorrespondenceReview] = []
    for edge in after.edges:
        if edge.field not in _ORIGIN_FIELDS:
            continue
        indexes = set(incoming[edge.path] + relevant[edge.owner] + relevant[edge.target]
                      + model_context[edge.owner[1]] + model_context[edge.target[1]])
        if edge.owner[0] == "model":
            indexes.update(model_children[edge.owner[1]])
        added = (edge.owner, edge.target, edge.field) not in old_links
        if not indexes and not added:
            continue
        failures = origin_findings[edge.path]
        status = "incumple" if any(finding["status"] == "incumple" for finding in failures) else "no_evaluable"
        reviews.append({
            "phase": "after", "status": status,
            "code": "invalid_affected_correspondence" if status == "incumple" else "correspondence_review_required",
            "dependent": _address(edge.owner), "target": _address(edge.target), "field": edge.field,
            "referencePath": edge.path, "changeIndexes": sorted(indexes),
        })
    if after.complete:
        for edge in before.edges:
            if edge.field in _ORIGIN_FIELDS and (edge.owner, edge.target, edge.field) not in new_links:
                # Unsupported/ambiguous survivors cannot prove that the old link was removed.
                if after.problems or after.ambiguous(edge.owner) or after.ambiguous(edge.target):
                    continue
                reviews.append({
                    "phase": "before", "status": "cumple", "code": "correspondence_removed",
                    "dependent": _address(edge.owner), "target": _address(edge.target), "field": edge.field,
                    "referencePath": edge.path, "changeIndexes": list(relevant[edge.owner]),
                })
    return reviews


def validate_transition(request: TransitionRequest, *, max_impacts: Optional[int] = None,
                        max_correspondence_checks: Optional[int] = 10_000,
                        max_constraint_checks: Optional[int] = 10_000,
                        max_version_checks: Optional[int] = 10_000,
                        max_trace_checks: Optional[int] = 10_000) -> TransitionResult:
    """Compare supplied captures without changing either one or accessing services."""
    if not isinstance(request, dict) or type(request.get("schemaVersion")) is not int or request["schemaVersion"] != 1:
        raise ValueError("La transición debe tener schemaVersion 1.")
    previous, current = _capture(request.get("before")), _capture(request.get("after"))
    if previous["projectId"] != current["projectId"]:
        raise ValueError("Las capturas deben corresponder al mismo proyecto.")
    if previous["snapshotId"] == current["snapshotId"]:
        raise ValueError("Las capturas deben tener snapshotId distintos.")
    before, after = _State(previous, "before"), _State(current, "after")
    changes = _changes(before, after)
    impacts = _incoming(changes, (before, after), max_impacts)
    before_validation, after_validation = _structural_capture(previous), _structural_capture(current)
    correspondence_request = {key: current[key] for key in ("schemaVersion", "projectId", "snapshotId", "scope", "project")}
    if "correspondenceProfile" in request:
        correspondence_request["correspondenceProfile"] = request["correspondenceProfile"]
    correspondence_validation = validate_correspondences(correspondence_request, max_checks=max_correspondence_checks)
    constraint_request = {key: current[key] for key in ("schemaVersion", "projectId", "snapshotId", "scope", "project")}
    if "constraintProfile" in request:
        constraint_request["constraintProfile"] = request["constraintProfile"]
    constraint_validation = validate_constraints(constraint_request, max_checks=max_constraint_checks)
    version_request = {key: current[key] for key in ("schemaVersion", "projectId", "snapshotId", "scope", "project")}
    if "versionContext" in request:
        version_request["versionContext"] = request["versionContext"]
    version_validation = validate_versions(version_request, max_checks=max_version_checks)
    trace_request = {"before": previous, "after": current}
    for key in ("attemptContext", "traceContext", "versionContext"):
        if key in request:
            trace_request[key] = request[key]
    trace_validation = validate_traceability(trace_request, changes, max_checks=max_trace_checks)
    delta = _structural_delta(before, after, before_validation, after_validation)
    reviews = _correspondence_reviews(before, after, changes, impacts, after_validation)
    findings: list[TransitionFinding] = []
    reference_failures: dict[tuple[Phase, str], TransitionFinding] = {}
    counts: dict[TransitionRuleId, Counter] = {rule: Counter() for rule in _RULES}

    def record(rules: list[TransitionRuleId], status: Status, code: str, description: str,
               state: _State, path: str, evidence: Optional[dict[str, Any]] = None) -> None:
        for rule in rules:
            counts[rule][status] += 1
        if status != "cumple":
            reference_failure = status == "incumple" and set(rules) <= {"RG14", "RG15"}
            prior = reference_failures.get((state.phase, path)) if reference_failure else None
            if prior is not None and set(rules) | set(prior["ruleIds"]) == {"RG14", "RG15"}:
                prior["ruleIds"] = ["RG14", "RG15"]
                prior["code"] = "invalid_correspondence_after_deletion"
                prior["description"] = "La eliminación dejó una correspondencia con destino inexistente."
                prior["evidence"].update(evidence or {})
                return
            finding: TransitionFinding = {
                "ruleIds": list(rules), "status": status, "severity": _SEVERITY[status],
                "code": code, "description": description, "phase": state.phase,
                "path": path, "evidence": evidence or {},
            }
            findings.append(finding)
            if reference_failure:
                reference_failures[(state.phase, path)] = finding

    for state in (before, after):
        affected_rules = list(_RULES) if state is after else ["RG13", "RG14", "RG15"]
        if not state.complete:
            record(affected_rules, "no_evaluable", "incomplete_capture",
                   "La cobertura parcial impide descartar cambios o referencias fuera de la captura.",
                   state, "/project", {"incompletePaths": list(state.gaps)})
        for code, path in state.problems:
            record(affected_rules, "no_evaluable", code,
                   "La identidad o el formato de referencia impide una comparación inequívoca.", state, path)
    for _ in changes:
        counts["RG13"]["cumple"] += 1

    for review in reviews:
        description = ("La correspondencia afectada tiene extremos inválidos."
                       if review["status"] == "incumple" else
                       "Se retiró la correspondencia del estado posterior."
                       if review["status"] == "cumple" else
                       "La correspondencia afectada requiere revisión semántica; la existencia de sus extremos no la demuestra.")
        record(["RG14"], review["status"], review["code"], description,
               before if review["phase"] == "before" else after, review["referencePath"], {
                   "dependent": review["dependent"], "target": review["target"], "changeIndexes": review["changeIndexes"],
               })
    reviewed_paths = {review["referencePath"] for review in reviews if review["phase"] == "after"}
    changed_keys = {_key(change["entity"]) for change in changes
                    if change["operation"] != "updated" or
                    (set(change["changedFields"]) | set(change["uncertainFields"])) - _PRESENTATION_FIELDS}
    for finding in after_validation["findings"]:
        if "RG05" not in finding["ruleIds"] or finding["status"] != "incumple":
            continue
        location, evidence = finding["location"], finding["evidence"]
        path, model_id = location["path"], location["modelId"]
        owner = ("element", model_id, location["elementId"]) if location["elementId"] is not None else ("model", model_id, model_id)
        target = ("element", evidence.get("targetModelId"), evidence.get("targetElementId"))
        root = path.rsplit("/", 1)[0] if path.endswith(("/modelId", "/elementId")) else path
        if (owner in changed_keys or target in changed_keys or ("model", model_id, model_id) in changed_keys) and root not in reviewed_paths:
            record(["RG14"], "incumple", "invalid_affected_correspondence", finding["description"],
                   after, path, {"structuralRuleIds": finding["ruleIds"], **evidence})

    after_impacts: dict[int, list[Impact]] = defaultdict(list)
    for impact in impacts:
        if impact["phase"] == "after":
            after_impacts[impact["changeIndex"]].append(impact)
    for position, change in enumerate(changes):
        key = _key(change["entity"])
        if change["operation"] != "deleted" or key[0] == "relationship":
            continue
        surviving = [impact for impact in after_impacts[position]
                     if (key == _key(impact["target"]) or key[0] == "model")
                     and before.unique(_key(impact["target"])) is not None]
        if surviving:
            for impact in surviving:
                record(["RG15"], "incumple", "reference_to_deleted_entity",
                       "La captura posterior conserva una referencia a un destino eliminado en esta transición.",
                       after, impact["referencePath"], {"changeIndex": position,
                       "deleted": change["entity"], "dependent": impact["dependent"], "target": impact["target"]})
        else:
            counts["RG15"]["cumple"] += 1

    if changes or before.problems or after.problems or not before.complete or not after.complete:
        states = {rule["status"] for rule in after_validation["rules"]}
        status = "incumple" if "incumple" in states else "no_evaluable" if "no_evaluable" in states else "cumple"
        failed = [rule["ruleId"] for rule in after_validation["rules"] if rule["status"] == "incumple"]
        unevaluable = [rule["ruleId"] for rule in after_validation["rules"] if rule["status"] == "no_evaluable"]
        description = (f"El estado posterior incumple las reglas estructurales {', '.join(failed)}."
                       if failed else f"La evaluación estructural posterior está incompleta para {', '.join(unevaluable)}."
                       if unevaluable else "El estado posterior cumple las comprobaciones estructurales RG01 a RG05 aplicables.")
        record(["RG16"], status, "post_change_structural_validation",
               description, after, "/project", {
                   "evaluatedRuleIds": list(_STRUCTURAL_RULES), "delta": dict(delta),
                   "failedRuleIds": failed, "unevaluableRuleIds": unevaluable,
               })

    rules = []
    for rule, description in _RULES.items():
        status = next((value for value in ("incumple", "no_evaluable", "cumple")
                       if counts[rule][value]), "no_aplica")
        rules.append({
            "ruleId": rule, "status": status, "severity": _SEVERITY[status],
            "description": description, "checkCounts": dict(counts[rule]),
        })
    return {
        "schemaVersion": 1, "kind": "transition", "projectId": previous["projectId"],
        "beforeSnapshotId": previous["snapshotId"], "afterSnapshotId": current["snapshotId"],
        "coverage": {"before": before.coverage(), "after": after.coverage()},
        "impactDepth": 1, "supportedReferenceFields": list(_FIELDS),
        "changes": changes, "impacts": impacts,
        "correspondenceReviews": reviews,
        "structuralValidation": {"before": before_validation, "after": after_validation, "delta": delta},
        "correspondenceValidation": correspondence_validation,
        "constraintValidation": constraint_validation,
        "versionValidation": version_validation,
        "traceabilityValidation": trace_validation,
        "rules": rules,
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analiza localmente RG13 a RG16 sobre dos capturas JSON.")
    parser.add_argument("transition", type=Path, help="JSON con schemaVersion, before y after, dentro del directorio de trabajo.")
    args = parser.parse_args()
    try:
        report = validate_transition(read_local_capture(args.transition))
    except (OSError, ValueError, RecursionError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

"""RG17-RG19 on supplied, scoped historical evidence; no history writes or rollback."""

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from .contracts import Status
from .correspondence import _equal
from .snapshot import MISSING, Model, Record, Snapshot, identifier
from .version_context import validate_context
from .version_contracts import VersionRequest, VersionResult


_RULES = {"RG17": "Compatibilidad explícita de revisiones de correspondencias.",
          "RG18": "Integridad de linaje y cambios asociados a revisiones aportadas.",
          "RG19": "Resolución de referencias en su contexto histórico declarado."}
_SEVERITY = {"cumple": "info", "incumple": "error", "no_evaluable": "warning", "no_aplica": "info"}


class VersionLimitExceeded(ValueError):
    pass


def _key(address: dict[str, Any]) -> tuple[str, str]:
    return address["modelId"], address["versionId"]


class _Revision:
    def __init__(self, entry: dict[str, Any]):
        self.entry = entry
        self.data = entry["model"]
        self.complete = entry["scope"] == "complete"
        self.entities: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        if self.data is not None:
            model = Model.from_record(Record(self.data, "/versionContext/revisions"))
            self.complete = self.complete and not model.element_gaps and not model.relationship_gaps
            for kind, records in (("element", model.elements), ("relationship", model.relationships.values())):
                for record in records:
                    if record.id is None:
                        self.complete = False
                    else:
                        self.entities[(kind, record.id)].append(record.data)
            self.complete = self.complete and all(len(values) == 1 for values in self.entities.values())

    def value(self, kind: str, entity_id: str) -> Any:
        if self.data is None:
            return None if self.complete else MISSING
        if kind == "model":
            return self.data if entity_id == self.entry["modelId"] else None if self.complete else MISSING
        values = self.entities.get((kind, entity_id), [])
        return values[0] if len(values) == 1 else None if not values and self.complete else MISSING


class _Evaluation:
    def __init__(self, request: VersionRequest, limit: Optional[int]):
        self.request = request
        self.snapshot = Snapshot.from_project(request["project"])
        self.context: Optional[dict[str, Any]] = None
        self.index: dict[tuple[str, str], list[_Revision]] = defaultdict(list)
        self.counts = {r: Counter() for r in _RULES}
        self.findings = []
        self.work, self.limit = 0, limit

    def tick(self) -> None:
        self.work += 1
        if self.limit is not None and self.work > self.limit:
            raise VersionLimitExceeded()

    def record(self, rule: str, status: Status, code: str, description: str, evidence: Optional[dict[str, Any]] = None) -> None:
        self.counts[rule][status] += 1
        if status != "cumple":
            evidence = copy.deepcopy(evidence or {})
            self.findings.append({"ruleIds": [rule], "status": status, "severity": _SEVERITY[status],
                                  "code": code, "description": description,
                                  "location": {"path": "/versionContext", "modelId": evidence.get("modelId"), "elementId": None, "referenceId": None},
                                  "evidence": evidence})

    def result(self) -> VersionResult:
        return {
            "schemaVersion": 1, "kind": "versions", "projectId": self.request["projectId"], "snapshotId": self.request["snapshotId"],
            "contextId": self.context["id"] if self.context else None,
            "coverage": {"requestedScope": self.request["scope"],
                         "effectiveScope": "complete" if self.request["scope"] == "complete" and not self.snapshot.incomplete_paths else "partial",
                         "models": [{"modelId": m.record.id, "path": m.record.path} for m in self.snapshot.models],
                         "incompletePaths": self.snapshot.incomplete_paths},
            "versionCoverage": {"catalogScope": self.context["scope"] if self.context else None,
                                "historyScope": self.context["historyScope"] if self.context else None,
                                "revisionCount": sum(len(values) for values in self.index.values())},
            "rules": [{"ruleId": rule, "status": next((s for s in ("incumple", "no_evaluable", "cumple") if self.counts[rule][s]), "no_aplica"),
                       "severity": _SEVERITY[next((s for s in ("incumple", "no_evaluable", "cumple") if self.counts[rule][s]), "no_aplica")],
                       "description": description, "checkCounts": dict(self.counts[rule]), "modelPaths": []} for rule, description in _RULES.items()],
            "findings": self.findings,
        }

    def unavailable(self, code: str, description: str) -> VersionResult:
        self.counts = {r: Counter() for r in _RULES}
        self.findings = []
        for rule in _RULES:
            self.record(rule, "no_evaluable", code, description)
        return self.result()

    def revision(self, key: tuple[str, str]) -> tuple[Status, Optional[_Revision]]:
        self.tick()
        values = self.index.get(key, [])
        if len(values) == 1:
            return "cumple", values[0]
        return ("incumple" if not values and self.context["scope"] == "complete" else "no_evaluable"), None

    def address(self, address: dict[str, Any]) -> Status:
        status, revision = self.revision(_key(address))
        if status != "cumple":
            return status
        value = revision.value(address["kind"], address["id"])
        return "no_evaluable" if value is MISSING else "incumple" if value is None else "cumple"

    def references(self) -> None:
        policies = defaultdict(list)
        for policy in self.context["compatibility"]:
            self.tick()
            policies[(_key(policy["source"]), _key(policy["target"]))].append(policy)
        for category, rule in (("correspondences", "RG17"), ("historicalReferences", "RG19")):
            for link in self.context[category]:
                self.tick()
                evidence = {"linkIds": [link["id"]], "versionIds": [link["source"]["versionId"], link["target"]["versionId"]],
                            "source": link["source"], "target": link["target"]}
                states = [self.address(link[field]) for field in ("source", "target")]
                status = "incumple" if "incumple" in states else "no_evaluable" if "no_evaluable" in states else "cumple"
                code, message = "unresolved_historical_reference", "No se resuelve inequívocamente un extremo dentro de su revisión y ámbito declarados."
                if status == "cumple" and rule == "RG17":
                    matches = policies.get((_key(link["source"]), _key(link["target"])), [])
                    if len(matches) != 1:
                        status, code, message = "no_evaluable", "unknown_version_compatibility", "Falta una política de compatibilidad única para este par dirigido de revisiones."
                    elif not matches[0]["compatible"]:
                        status, code, message = "incumple", "incompatible_versions", "La correspondencia conecta revisiones declaradas incompatibles."
                self.record(rule, status, code, message, evidence)
                if status == "cumple" and any(not self.index[_key(link[field])][0].complete for field in ("source", "target")):
                    self.record(rule, "no_evaluable", "partial_historical_revision", "Una revisión parcial impide certificar completamente sus referencias.", evidence)

    def lineage(self) -> None:
        graph = {}
        for key, values in self.index.items():
            self.tick()
            if len(values) != 1:
                self.record("RG18", "no_evaluable", "ambiguous_revision", "El identificador de revisión se repite dentro del mismo modelo.", {"modelId": key[0], "versionId": key[1]})
                continue
            revision = values[0]
            parents = revision.entry["parentVersionIds"]
            graph[key] = [(key[0], parent) for parent in parents]
            if len(parents) > 1:
                self.record("RG18", "no_evaluable", "unsupported_history_merge", "La reconstrucción de una fusión necesita una política de merge explícita.", {"modelId": key[0], "versionId": key[1]})
            for parent in parents:
                status, prior = self.revision((key[0], parent))
                self.record("RG18", status, "unresolved_parent_revision", "No se resuelve inequívocamente la revisión padre declarada.", {"modelId": key[0], "versionId": key[1], "parentVersionId": parent})
                if prior is not None and (not prior.complete or not revision.complete):
                    self.record("RG18", "no_evaluable", "partial_history_revision", "Falta una captura completa para reconstruir este paso histórico.", {"modelId": key[0], "versionId": key[1]})
        active, done = set(), set()

        def visit(key):
            self.tick()
            if key in active:
                return False
            if key in done or key not in graph:
                return True
            active.add(key)
            if not all(visit(parent) for parent in graph[key]):
                return False
            active.remove(key)
            done.add(key)
            return True

        if not all(visit(key) for key in graph):
            self.record("RG18", "incumple", "cyclic_revision_history", "El linaje declarado contiene un ciclo; no permite reconstruir una evolución acíclica.")

    def changes(self) -> dict[tuple[str, Optional[str], str], set[tuple[str, str]]]:
        covered = defaultdict(set)
        identities = Counter((e["modelId"], e.get("fromVersionId", MISSING), e.get("toVersionId", MISSING), e["entityType"], e["entityId"]) for e in self.context["changes"])
        for event in self.context["changes"]:
            self.tick()
            model_id, kind, entity_id = event["modelId"], event["entityType"], event["entityId"]
            evidence = {"changeIds": [event["id"]], "modelId": model_id, "fromVersionId": event.get("fromVersionId"), "toVersionId": event.get("toVersionId")}
            if event["projectId"] != self.request["projectId"]:
                self.record("RG18", "incumple", "foreign_history_change", "El cambio pertenece a otro proyecto.", evidence)
                continue
            if ("fromVersionId" not in event or not event.get("toVersionId") or event.get("valueScope", "partial") != "entity"
                    or kind not in ("model", "element", "relationship")
                    or any(v is not None and not isinstance(v, dict) for v in (event["oldValue"], event["newValue"]))
                    or event["oldValue"] is None and event["newValue"] is None):
                self.record("RG18", "no_evaluable", "unversioned_or_partial_change", "Falta asociación explícita a revisiones, valores de entidad completos o un tipo de entidad soportado.", evidence)
                continue
            identity = (model_id, event["fromVersionId"], event["toVersionId"], kind, entity_id)
            if identities[identity] != 1:
                self.record("RG18", "no_evaluable", "ambiguous_history_changes", "Varios cambios cubren la misma entidad y paso sin una secuencia intermedia verificable.", evidence)
                continue
            status, after = self.revision((model_id, event["toVersionId"]))
            before = None
            if event["fromVersionId"] is not None:
                previous_status, before = self.revision((model_id, event["fromVersionId"]))
                status = "incumple" if "incumple" in (status, previous_status) else "no_evaluable" if "no_evaluable" in (status, previous_status) else "cumple"
            if status != "cumple":
                self.record("RG18", status, "unresolved_change_revision", "No se resuelve inequívocamente una revisión asociada al cambio.", evidence)
                continue
            parents = after.entry["parentVersionIds"]
            if (before is None and (parents or kind != "model")) or (before is not None and event["fromVersionId"] not in parents):
                self.record("RG18", "incumple", "change_lineage_mismatch", "El cambio no corresponde a un paso padre-hijo o creación inicial de modelo declarado.", evidence)
                continue
            old = before.value(kind, entity_id) if before is not None else None
            new = after.value(kind, entity_id)
            if old is MISSING or new is MISSING or not after.complete or (before is not None and not before.complete):
                self.record("RG18", "no_evaluable", "incomplete_change_values", "Las capturas no permiten verificar los valores históricos completos.", evidence)
            elif not _equal(old, event["oldValue"]) or not _equal(new, event["newValue"]):
                self.record("RG18", "incumple", "history_value_mismatch", "Los valores registrados no coinciden con sus revisiones de origen y destino.", evidence)
            else:
                self.record("RG18", "cumple", "verified_history_change", "El cambio conserva su asociación y sus valores históricos.", evidence)
                covered[(model_id, event["fromVersionId"], event["toVersionId"])].add((kind, entity_id))
        return covered

    def reconstruction(self, covered) -> None:
        for key, values in self.index.items():
            if len(values) != 1:
                continue
            after = values[0]
            parents = after.entry["parentVersionIds"]
            if len(parents) != 1 or not after.complete:
                continue
            status, before = self.revision((key[0], parents[0]))
            if status != "cumple" or not before.complete:
                continue
            edge_coverage = covered[(key[0], parents[0], key[1])]
            if ("model", key[0]) in edge_coverage:
                continue
            expected = []
            if before.data is None or after.data is None:
                if not _equal(before.data, after.data):
                    expected.append(("model", key[0]))
            else:
                metadata = lambda data: {k: v for k, v in data.items() if k not in ("elements", "relationships")}
                if not _equal(metadata(before.data), metadata(after.data)):
                    expected.append(("model", key[0]))
                for collection in ("elements", "relationships"):
                    self.tick()
                    old_order = [entity["id"] for entity in before.data[collection]]
                    new_order = [entity["id"] for entity in after.data[collection]]
                    common = set(old_order) & set(new_order)
                    if ([item for item in old_order if item in common] != [item for item in new_order if item in common]
                            and ("model", key[0]) not in expected):
                        expected.append(("model", key[0]))
                for entity in before.entities.keys() | after.entities.keys():
                    self.tick()
                    if not _equal(before.value(*entity), after.value(*entity)):
                        expected.append(entity)
            for kind, entity_id in expected:
                if (kind, entity_id) not in edge_coverage:
                    candidates = [e for e in self.context["changes"] if e["modelId"] == key[0]
                                  and ((e["entityType"], e["entityId"]) in ((kind, entity_id), ("model", key[0]))
                                       or e["entityType"] not in ("model", "element", "relationship") and e["entityId"] == entity_id)
                                  and ("fromVersionId" not in e or e["fromVersionId"] == parents[0])
                                  and (not e.get("toVersionId") or e["toVersionId"] == key[1])]
                    status = "incumple" if self.context["historyScope"] == "complete" and not candidates else "no_evaluable"
                    self.record("RG18", status, "unrecorded_revision_change" if not candidates else "unverified_revision_change", "Un cambio entre revisiones no tiene un registro completo verificable en el historial aportado.",
                                {"modelId": key[0], "fromVersionId": parents[0], "toVersionId": key[1], "entityType": kind, "entityId": entity_id})


def validate_versions(request: VersionRequest, *, max_checks: Optional[int] = 10_000) -> VersionResult:
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
    if "versionContext" not in request:
        return evaluation.unavailable("missing_version_context", "Faltan revisiones y asociaciones históricas explícitas; los IDs de captura no son versiones.")
    try:
        context = validate_context(request["versionContext"], request["projectId"])
    except (ValueError, RecursionError) as error:
        return evaluation.unavailable("invalid_version_context", f"El contexto histórico no es evaluable. {error}")
    evaluation.context = context
    try:
        for entry in context["revisions"]:
            evaluation.tick()
            evaluation.index[_key(entry)].append(_Revision(entry))
        active_history = context["changes"] or any(e["parentVersionIds"] for e in context["revisions"])
        for rule, active in (("RG17", context["correspondences"]), ("RG18", active_history), ("RG19", context["historicalReferences"])):
            if active and (context["scope"] == "partial" or (rule == "RG18" and context["historyScope"] == "partial")):
                evaluation.record(rule, "no_evaluable", "partial_version_context", "El ámbito parcial del catálogo o historial impide certificar una evaluación completa.")
        evaluation.references()
        evaluation.lineage()
        covered = evaluation.changes()
        evaluation.reconstruction(covered)
    except VersionLimitExceeded:
        return evaluation.unavailable("version_evaluation_limit", "Se agotó el presupuesto histórico; no se presenta una evaluación parcial como completa.")
    return evaluation.result()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalúa RG17 a RG19 sobre un contexto histórico explícito y local.")
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    try:
        report = validate_versions(json.loads(args.snapshot.read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

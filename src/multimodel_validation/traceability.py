"""Observed-change traceability without persistence, inferred origins or automatic repair."""

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from .contracts import Status
from .correspondence import _equal
from .snapshot import MISSING, Snapshot
from .trace_context import trace_id, validate_attempt, validate_context
from .trace_contracts import TraceResult
from .version_context import validate_context as validate_versions_context


_RULES = {"RG20": "Asociación declarada entre origen e intento de cambio observado.",
          "RG21": "Identificación de cambios y vínculo comprobable con registros históricos aportados.",
          "RG22": "Integridad de extremos y tipos de enlaces de trazabilidad explícitos."}
_SEVERITY = {"cumple": "info", "incumple": "error", "no_evaluable": "warning", "no_aplica": "info"}
_ROLES = {"requested-by": ({"change", "history"}, {"origin"}),
          "recorded-in": ({"change"}, {"history"}),
          "affects": ({"change", "history"}, {"artifact"}),
          "related-to": ({"requirement"}, {"origin", "artifact", "version"}),
          "revision-of": ({"version"}, {"artifact"})}


class TraceLimitExceeded(ValueError):
    pass


class _Evaluation:
    def __init__(self, request: dict[str, Any], changes: list[dict[str, Any]], limit: Optional[int]):
        self.request, self.changes = request, changes
        self.before, self.after = request["before"], request["after"]
        self.snapshots = {phase: Snapshot.from_project(request[phase]["project"]) for phase in ("before", "after")}
        self.context = None
        self.attempt = None
        self.attempt_id = trace_id(request.get("attemptContext", {}).get("id")) if isinstance(request.get("attemptContext"), dict) else None
        self.context_id = trace_id(request.get("traceContext", {}).get("id")) if isinstance(request.get("traceContext"), dict) else None
        self.counts = {r: Counter() for r in _RULES}
        self.findings, self.traces, self.valid_links = [], [], []
        self.records = {category: defaultdict(list) for category in ("origins", "requirements", "history")}
        self.version_index = None
        self.version_scope = None
        self.work, self.limit = 0, limit

    def tick(self):
        self.work += 1
        if self.limit is not None and self.work > self.limit:
            raise TraceLimitExceeded()

    def record(self, rule: str, status: Status, code: str, description: str, evidence: Optional[dict[str, Any]] = None):
        self.counts[rule][status] += 1
        if status != "cumple":
            evidence = copy.deepcopy(evidence or {})
            self.findings.append({"ruleIds": [rule], "status": status, "severity": _SEVERITY[status], "code": code,
                                  "description": description, "location": {"path": "/traceContext", "modelId": evidence.get("modelId"), "elementId": None, "referenceId": None}, "evidence": evidence})

    def result(self) -> TraceResult:
        snapshot = self.snapshots["after"]
        rules = []
        for rule, description in _RULES.items():
            status = next((s for s in ("incumple", "no_evaluable", "cumple") if self.counts[rule][s]), "no_aplica")
            rules.append({"ruleId": rule, "status": status, "severity": _SEVERITY[status], "description": description,
                          "checkCounts": dict(self.counts[rule]), "modelPaths": []})
        return {"schemaVersion": 1, "kind": "traceability", "projectId": self.after["projectId"], "snapshotId": self.after["snapshotId"],
                "beforeSnapshotId": self.before["snapshotId"], "attemptId": self.attempt_id, "contextId": self.context_id,
                "coverage": {"requestedScope": self.after["scope"], "effectiveScope": "complete" if self.after["scope"] == "complete" and not snapshot.incomplete_paths else "partial",
                             "models": [{"modelId": m.record.id, "path": m.record.path} for m in snapshot.models], "incompletePaths": snapshot.incomplete_paths},
                "traceCoverage": {"contextAccepted": self.context is not None, "contextScope": self.context["scope"] if self.context else None,
                                  "observedChangeCount": len(self.changes)},
                "traces": self.traces, "rules": rules, "findings": self.findings}

    def entity_value(self, entity, phase):
        self.tick()
        snapshot = self.snapshots[phase]
        complete = self.request[phase]["scope"] == "complete" and not snapshot.incomplete_paths
        models = snapshot.models_by_id.get(entity["modelId"], [])
        if len(models) != 1:
            return None if not models and complete else MISSING
        model = models[0]
        if entity["kind"] == "model":
            value = model.record.data if entity["id"] == model.record.id else None
        elif entity["kind"] == "element":
            records = model.elements_by_id.get(entity["id"], [])
            value = records[0].data if len(records) == 1 else None if not records and not model.element_gaps else MISSING
        else:
            records = []
            for record in model.relationships.values():
                self.tick()
                if record.id is None:
                    complete = False
                elif record.id == entity["id"]:
                    records.append(record)
            value = records[0].data if len(records) == 1 else None if not records and not model.relationship_gaps else MISSING
        return value if complete else MISSING

    def lookup_record(self, category, record_id):
        self.tick()
        values = self.records[category].get(record_id, [])
        if len(values) == 1:
            record = values[0]
            return ("cumple" if record["projectId"] == self.after["projectId"] else "incumple"), record
        return ("incumple" if not values and self.context and self.context["scope"] == "complete" else "no_evaluable"), None

    def resolve(self, ref):
        self.tick()
        kind = ref["kind"]
        if kind == "change":
            return ("cumple", self.changes[ref["index"]]) if ref["index"] < len(self.changes) else ("incumple", None)
        if kind == "artifact":
            value = self.entity_value(ref["entity"], ref["phase"])
            return ("no_evaluable" if value is MISSING else "incumple" if value is None else "cumple"), value
        if kind == "origin":
            if ref["catalog"] == "attempt":
                origin = self.attempt.get("origin") if self.attempt else None
                return ("no_evaluable", None) if origin is None else ("cumple", origin) if origin["id"] == ref["id"] else ("incumple", None)
            return self.lookup_record("origins", ref["id"])
        if kind == "history":
            return self.lookup_record("history", ref["id"])
        if kind == "requirement":
            status, record = self.lookup_record("requirements", ref["id"])
            return (status, record) if status != "cumple" else (self.resolve(record["artifact"])[0], record)
        if self.version_index is None:
            self.version_index = defaultdict(list)
            try:
                context = validate_versions_context(self.request.get("versionContext"), self.after["projectId"])
            except (ValueError, RecursionError):
                return "no_evaluable", None
            self.version_scope = context["scope"]
            for revision in context["revisions"]:
                self.tick()
                self.version_index[(revision["modelId"], revision["versionId"])].append(revision)
        revisions = self.version_index.get((ref["modelId"], ref["versionId"]), [])
        if len(revisions) == 1:
            return ("cumple" if self.version_scope == "complete" else "no_evaluable"), revisions[0]
        return ("incumple" if not revisions and self.version_scope == "complete" else "no_evaluable"), None

    @staticmethod
    def identity(kind, value):
        if kind == "change":
            return value["entity"]
        if kind == "history" and value["entityType"] in ("model", "element", "relationship"):
            return {"kind": value["entityType"], "modelId": value["modelId"], "id": value["entityId"]}
        return None

    def links(self):
        if self.context is None:
            self.record("RG22", "no_evaluable", "missing_trace_context", "Falta un contexto evaluable de enlaces de trazabilidad.")
            return
        if self.context["scope"] == "partial":
            self.record("RG22", "no_evaluable", "partial_trace_context", "El catálogo parcial impide certificar todos los enlaces declarados.")
        for link in self.context["links"]:
            self.tick()
            relation, source, target = link["relation"], link["source"], link["target"]
            evidence = {"linkIds": [link["id"]]}
            roles = _ROLES.get(relation)
            if roles is None:
                self.record("RG22", "no_evaluable", "unsupported_trace_relation", "El tipo de enlace necesita un contrato explícito soportado.", evidence)
                continue
            if source["kind"] not in roles[0] or target["kind"] not in roles[1]:
                self.record("RG22", "incumple", "invalid_trace_roles", "Los extremos no son compatibles con el tipo de enlace declarado.", evidence)
                continue
            left_status, left = self.resolve(source)
            right_status, right = self.resolve(target)
            status = "incumple" if "incumple" in (left_status, right_status) else "no_evaluable" if "no_evaluable" in (left_status, right_status) else "cumple"
            if status == "cumple" and relation in ("recorded-in", "affects"):
                expected = self.identity(source["kind"], left)
                actual = self.identity("history", right) if relation == "recorded-in" else target["entity"]
                status = "no_evaluable" if expected is None or actual is None else "cumple" if _equal(expected, actual) else "incumple"
            if status == "cumple" and relation == "revision-of":
                status = "cumple" if target["entity"]["kind"] == "model" and target["entity"]["modelId"] == source["modelId"] and target["entity"]["id"] == source["modelId"] else "incumple"
            self.record("RG22", status, "invalid_trace_link", "El enlace no se resuelve inequívocamente o no mantiene la identidad del artefacto declarado.", evidence)
            if status == "cumple":
                self.valid_links.append(link)

    def origin_refs(self, index, verified_history_ids):
        origins = []
        if self.attempt and self.attempt.get("origin"):
            origins.append({"kind": "origin", "catalog": "attempt", "id": self.attempt["origin"]["id"]})
        for link in self.valid_links:
            self.tick()
            if link["relation"] == "requested-by" and ((link["source"]["kind"] == "change" and link["source"]["index"] == index)
                                                      or (link["source"]["kind"] == "history" and link["source"]["id"] in verified_history_ids)):
                if link["target"] not in origins:
                    origins.append(link["target"])
        return origins

    def verify_history(self, index, trace):
        change = self.changes[index]
        evidence = {"changeIndices": [index], "modelId": change["entity"]["modelId"]}
        links = [link for link in self.context["links"] if link["relation"] == "recorded-in" and link["source"]["kind"] == "change" and link["source"]["index"] == index] if self.context else []
        if not links:
            status = "incumple" if self.context and self.context["scope"] == "complete" else "no_evaluable"
            self.record("RG21", status, "unlinked_history_change", "El cambio observado no tiene una asociación verificable con el historial aportado.", evidence)
            return
        for link in links:
            self.tick()
            if link not in self.valid_links:
                status = "no_evaluable"
                if link["target"]["kind"] == "history":
                    record_status, history = self.lookup_record("history", link["target"]["id"])
                    if record_status == "incumple" or (record_status == "cumple" and self.identity("history", history) is not None and not _equal(change["entity"], self.identity("history", history))):
                        status = "incumple"
                self.record("RG21", status, "unverified_history_link", "El enlace al historial no permite comprobar la asociación del cambio.", evidence)
                continue
            _, history = self.lookup_record("history", link["target"]["id"])
            if (change.get("uncertainFields") or history.get("valueScope", "partial") != "entity"
                    or any(v is not None and not isinstance(v, dict) for v in (history["oldValue"], history["newValue"]))):
                self.record("RG21", "no_evaluable", "partial_trace_values", "Faltan valores completos o datos ciertos del cambio para comprobar su historial.", evidence)
                continue
            old = self.entity_value(change["entity"], "before")
            new = self.entity_value(change["entity"], "after")
            if old is MISSING or new is MISSING:
                self.record("RG21", "no_evaluable", "incomplete_trace_capture", "Las capturas no permiten comprobar las entidades completas.", evidence)
            elif not _equal(old, history["oldValue"]) or not _equal(new, history["newValue"]):
                self.record("RG21", "incumple", "trace_history_value_mismatch", "El registro histórico no coincide con los valores del cambio observado.", {**evidence, "historyIds": [history["id"]]})
            else:
                self.record("RG21", "cumple", "verified_trace_history", "La entidad y sus valores se relacionan con el registro histórico aportado.", evidence)
                trace["historyIds"].append(history["id"])
        if self.context and self.context["scope"] == "partial":
            self.record("RG21", "no_evaluable", "partial_trace_history", "El contexto parcial no certifica la trazabilidad completa del cambio.", evidence)

    def observed(self):
        for index, change in enumerate(self.changes):
            self.tick()
            trace = {"id": f"{self.attempt_id or 'observed'}/change/{index}", "changeIndex": index,
                     "operation": change["operation"], "entity": copy.deepcopy(change["entity"]),
                     "originRefs": [], "historyIds": []}
            self.traces.append(trace)
            self.verify_history(index, trace)
            origins = self.origin_refs(index, trace["historyIds"])
            certain = change["operation"] != "updated" or bool(change.get("changedFields"))
            if origins and certain:
                trace["originRefs"] = origins
                self.record("RG20", "cumple", "declared_attempt_origin", "Las diferencias observadas están asociadas al origen declarado del intento.")
            else:
                self.record("RG20", "no_evaluable", "unknown_change_origin", "Falta un origen verificable en el contexto o solo hay una modificación posible.", {"changeIndices": [index]})


def validate_traceability(request: dict[str, Any], observed_changes: list[dict[str, Any]], *, max_checks: Optional[int] = 10_000) -> TraceResult:
    """Host supplies changes computed by the existing transition engine, never by HTTP input."""
    if max_checks is not None and (type(max_checks) is not int or max_checks < 0):
        raise ValueError("max_checks debe ser un entero no negativo.")
    evaluation = _Evaluation(request, observed_changes, max_checks)
    if "attemptContext" in request:
        try:
            evaluation.attempt = validate_attempt(request["attemptContext"], request["before"], request["after"])
        except ValueError:
            if observed_changes:
                evaluation.record("RG20", "no_evaluable", "invalid_attempt_context", "Los metadatos del intento no corresponden a estas capturas.")
    if "traceContext" in request:
        try:
            evaluation.context = validate_context(request["traceContext"], request["after"]["projectId"])
        except (ValueError, RecursionError) as error:
            evaluation.record("RG22", "no_evaluable", "invalid_trace_context", f"El contexto de trazabilidad no es evaluable. {error}")
    try:
        if evaluation.context:
            for category in ("origins", "requirements", "history"):
                for record in evaluation.context[category]:
                    evaluation.tick()
                    evaluation.records[category][record["id"]].append(record)
        evaluation.links()
        evaluation.observed()
    except TraceLimitExceeded:
        evaluation.counts = {r: Counter() for r in _RULES}
        evaluation.findings, evaluation.traces = [], []
        for rule in _RULES:
            evaluation.record(rule, "no_evaluable", "trace_evaluation_limit", "Se agotó el presupuesto de trazabilidad; no se presenta una evaluación parcial como completa.")
    return evaluation.result()


def main():
    from .transition import validate_transition

    parser = argparse.ArgumentParser(description="Evalúa RG20 a RG22 sobre una transición local sin servicios.")
    parser.add_argument("transition", type=Path)
    args = parser.parse_args()
    try:
        report = validate_transition(json.loads(args.transition.read_text(encoding="utf-8")))["traceabilityValidation"]
    except (OSError, ValueError, RecursionError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

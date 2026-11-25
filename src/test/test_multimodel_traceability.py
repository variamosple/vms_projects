import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.multimodel_validation.transition import validate_transition


DEMO = Path(__file__).parent / "fixtures" / "multimodel_traceability.json"
RULES = ("RG20", "RG21", "RG22")


@pytest.fixture
def transition_request():
    return json.loads(DEMO.read_text(encoding="utf-8"))


def report(transition_request, **kwargs):
    return validate_transition(transition_request, **kwargs)["traceabilityValidation"]


def statuses(result):
    return {r["ruleId"]: r["status"] for r in result["rules"]}


def test_explicit_request_history_requirement_artifact_and_version_links_pass(transition_request):
    original = copy.deepcopy(transition_request)
    result = report(transition_request)
    assert statuses(result) == dict.fromkeys(RULES, "cumple")
    assert result["findings"] == []
    assert result["traceCoverage"]["observedChangeCount"] == 1
    assert result["traces"][0]["entity"] == {"kind": "element", "modelId": "base", "id": "catalog"}
    assert result["traces"][0]["historyIds"] == ["rename-catalog"]
    assert result["traces"][0]["originRefs"] == [{"kind": "origin", "catalog": "attempt", "id": "chat-interaction"}]
    assert transition_request == original


def test_automatic_attempt_correlates_origin_without_inventing_history(transition_request):
    del transition_request["traceContext"]
    result = report(transition_request)
    assert statuses(result) == {"RG20": "cumple", "RG21": "no_evaluable", "RG22": "no_evaluable"}
    assert result["traces"][0]["historyIds"] == []
    assert "CatalogoWeb" not in json.dumps(result)  # Only metadata; no entity values or prompt copied to traces.


def test_no_attempt_metadata_does_not_invent_an_origin(transition_request):
    del transition_request["attemptContext"];del transition_request["traceContext"]
    result = report(transition_request)
    assert statuses(result) == dict.fromkeys(RULES, "no_evaluable")
    assert result["traces"][0]["originRefs"] == []


def test_no_observed_change_has_no_origin_or_history_obligation(transition_request):
    transition_request["after"]["project"] = copy.deepcopy(transition_request["before"]["project"])
    transition_request["traceContext"]["links"] = []
    result = report(transition_request)
    assert statuses(result) == dict.fromkeys(RULES, "no_aplica")
    assert result["traces"] == []


@pytest.mark.parametrize("problem", ["version", "project", "before", "after", "prompt", "bad-origin", "oversized-id"])
def test_invalid_attempt_metadata_cannot_attest_origin(transition_request, problem):
    a = transition_request["attemptContext"]
    if problem == "version": a["schemaVersion"] = True
    elif problem == "project": a["projectId"] = "other-project"
    elif problem == "before": a["beforeSnapshotId"] = "wrong"
    elif problem == "after": a["afterSnapshotId"] = "wrong"
    elif problem == "prompt": a["prompt"] = "private transition_request text"
    elif problem == "bad-origin": a["origin"]["kind"] = "invented"
    else: a["id"] = "x" * 257
    assert statuses(report(transition_request))["RG20"] == "no_evaluable"


def test_explicit_catalog_origin_can_be_used_without_automatic_metadata(transition_request):
    del transition_request["attemptContext"]
    link = next(l for l in transition_request["traceContext"]["links"] if l["id"] == "change-origin")
    link["target"] = {"kind": "origin", "catalog": "context", "id": "business-request"}
    assert statuses(report(transition_request)) == dict.fromkeys(RULES, "cumple")


def test_history_origin_chain_requires_a_verified_corresponding_history_record(transition_request):
    del transition_request["attemptContext"]
    link = next(l for l in transition_request["traceContext"]["links"] if l["id"] == "change-origin")
    link.update(source={"kind": "history", "id": "rename-catalog"}, target={"kind": "origin", "catalog": "context", "id": "business-request"})
    assert statuses(report(transition_request))["RG20"] == "cumple"
    transition_request["traceContext"]["history"][0]["oldValue"]["name"] = "Different change"
    result = statuses(report(transition_request))
    assert result["RG21"] == "incumple"
    assert result["RG20"] == "no_evaluable"


@pytest.mark.parametrize("scope,expected", [("complete", "incumple"), ("partial", "no_evaluable")])
def test_missing_record_respects_trace_catalog_coverage(transition_request, scope, expected):
    transition_request["traceContext"]["scope"] = scope
    transition_request["traceContext"]["history"] = []
    result = statuses(report(transition_request))
    assert result["RG20"] == "cumple"
    assert result["RG21"] == result["RG22"] == expected


def test_wrong_artifact_identity_is_a_broken_history_association(transition_request):
    transition_request["traceContext"]["history"][0].update(modelId="derived", entityId="service")
    result = statuses(report(transition_request))
    assert result["RG21"] == result["RG22"] == "incumple"


@pytest.mark.parametrize("field", ["oldValue", "newValue"])
def test_history_values_must_match_the_observed_change(transition_request, field):
    transition_request["traceContext"]["history"][0][field]["name"] = "Wrong value"
    result = report(transition_request)
    assert statuses(result)["RG21"] == "incumple"
    assert result["traces"][0]["historyIds"] == []


@pytest.mark.parametrize("problem", ["partial", "missing-scope", "scalar-entity"])
def test_incomplete_history_values_are_not_treated_as_full_entities(transition_request, problem):
    event = transition_request["traceContext"]["history"][0]
    if problem == "partial": event["valueScope"] = "partial"
    elif problem == "missing-scope": del event["valueScope"]
    else: event["oldValue"] = "Catalogo"
    assert statuses(report(transition_request))["RG21"] == "no_evaluable"


def test_no_history_link_in_explicit_complete_context_is_a_reported_gap(transition_request):
    transition_request["traceContext"]["links"] = [l for l in transition_request["traceContext"]["links"] if l["relation"] != "recorded-in"]
    assert statuses(report(transition_request))["RG21"] == "incumple"


@pytest.mark.parametrize("category", ["origins", "requirements", "history"])
def test_duplicate_record_ids_are_ambiguous_and_never_overwritten(transition_request, category):
    transition_request["traceContext"][category].append(copy.deepcopy(transition_request["traceContext"][category][0]))
    assert statuses(report(transition_request))["RG22"] == "no_evaluable"


def test_attempt_origin_namespace_is_separate_from_loaded_records(transition_request):
    transition_request["traceContext"]["origins"].append({"id": "chat-interaction", "kind": "event", "projectId": "foreign-project"})
    assert statuses(report(transition_request))["RG20"] == "cumple"


@pytest.mark.parametrize("problem", ["foreign-origin", "missing-requirement", "missing-artifact", "bad-roles", "wrong-version-model", "missing-change-index"])
def test_broken_trace_endpoints_and_relationships_are_visible(transition_request, problem):
    c = transition_request["traceContext"]
    if problem == "foreign-origin": c["origins"][0]["projectId"] = "other-project"
    elif problem == "missing-requirement": c["requirements"] = []
    elif problem == "missing-artifact": c["requirements"][0]["artifact"]["entity"]["id"] = "unknown"
    elif problem == "bad-roles": c["links"][0]["source"] = {"kind": "requirement", "id": "catalog-requirement"}
    elif problem == "wrong-version-model": c["links"][4]["target"]["entity"] = {"kind": "model", "modelId": "derived", "id": "derived"}
    else: c["links"][0]["source"]["index"] = 99
    assert statuses(report(transition_request))["RG22"] == "incumple"


def test_unknown_relationship_contract_is_unevaluable_not_a_silent_pass(transition_request):
    transition_request["traceContext"]["links"][0]["relation"] = "custom-language-relation"
    assert statuses(report(transition_request))["RG22"] == "no_evaluable"


@pytest.mark.parametrize("mode", ["missing", "invalid", "unknown-revision"])
def test_version_links_use_only_the_explicit_version_catalog(transition_request, mode):
    if mode == "missing": del transition_request["versionContext"]
    elif mode == "invalid": transition_request["versionContext"]["schemaVersion"] = 99
    else: transition_request["traceContext"]["links"][3]["target"]["versionId"] = "not-present"
    assert statuses(report(transition_request))["RG22"] == ("incumple" if mode == "unknown-revision" else "no_evaluable")


def test_partial_snapshots_cannot_claim_verified_history_values(transition_request):
    transition_request["after"]["scope"] = "partial"
    result = statuses(report(transition_request))
    assert result["RG20"] == "cumple"  # The name difference is known; causality is only declared.
    assert result["RG21"] == result["RG22"] == "no_evaluable"


def test_possible_nested_modification_is_not_attested_as_a_confirmed_change(transition_request):
    for phase in ("before", "after"):
        element = transition_request[phase]["project"]["productLines"][0]["domainEngineering"]["models"][0]["elements"][0]
        element["name"] = "Same"
        element["properties"] = [{"name": "Selected", "value": "yes"}]
    transition_request["after"]["scope"] = "partial"
    transition_request["after"]["project"]["productLines"][0]["domainEngineering"]["models"][0]["elements"][0]["properties"] = []
    result = report(transition_request)
    assert result["traceCoverage"]["observedChangeCount"] == 1
    assert statuses(result)["RG20"] == statuses(result)["RG21"] == "no_evaluable"


@pytest.mark.parametrize("operation", ["created", "deleted"])
def test_whole_model_creation_and_deletion_have_one_trace_and_correct_history(transition_request, operation):
    c = transition_request["traceContext"]
    source = "after" if operation == "created" else "before"
    model = copy.deepcopy(transition_request[source]["project"]["productLines"][0]["domainEngineering"]["models"][0])
    absent = "before" if operation == "created" else "after"
    transition_request[absent]["project"]["productLines"][0]["domainEngineering"]["models"] = []
    event = c["history"][0]
    event.update(entityType="model", entityId="base", oldValue=None if operation == "created" else model,
                 newValue=model if operation == "created" else None)
    for link in c["links"]:
        if link["id"] in ("history-artifact", "version-model"):
            link["target"] = {"kind": "artifact", "phase": source, "entity": {"kind": "model", "modelId": "base", "id": "base"}}
    c["requirements"][0]["artifact"]["phase"] = source
    result = report(transition_request)
    assert statuses(result) == dict.fromkeys(RULES, "cumple")
    assert len(result["traces"]) == 1
    assert result["traces"][0]["operation"] == operation


@pytest.mark.parametrize("problem", ["version", "project", "unknown-field", "invalid-kind", "boolean-index", "duplicate-link", "too-many-links", "non-json", "long-id"])
def test_invalid_loaded_context_preserves_the_automatic_attempt_and_prior_reports(transition_request, problem):
    c = transition_request["traceContext"]
    if problem == "version": c["schemaVersion"] = True
    elif problem == "project": c["projectId"] = "another"
    elif problem == "unknown-field": c["execute"] = "never run"
    elif problem == "invalid-kind": c["links"][0]["target"]["kind"] = "guess"
    elif problem == "boolean-index": c["links"][0]["source"]["index"] = False
    elif problem == "duplicate-link": c["links"].append(copy.deepcopy(c["links"][0]))
    elif problem == "too-many-links": c["links"] *= 86
    elif problem == "non-json": c["history"][0]["oldValue"]["number"] = float("nan")
    else: c["id"] = "x" * 257
    result = report(transition_request)
    assert statuses(result) == {"RG20": "cumple", "RG21": "no_evaluable", "RG22": "no_evaluable"}


@pytest.mark.parametrize("mode", ["valid", "absent", "invalid", "history-mismatch", "bad-attempt", "limit"])
def test_sixth_context_does_not_change_any_of_the_five_prior_reports(transition_request, mode):
    baseline = validate_transition({k: v for k, v in transition_request.items() if k not in ("traceContext", "attemptContext")})
    if mode == "absent": del transition_request["traceContext"];del transition_request["attemptContext"]
    elif mode == "invalid": transition_request["traceContext"]["schemaVersion"] = 0
    elif mode == "history-mismatch": transition_request["traceContext"]["history"][0]["oldValue"]["name"] = "Other"
    elif mode == "bad-attempt": transition_request["attemptContext"]["afterSnapshotId"] = "other"
    result = validate_transition(transition_request, max_trace_checks=0 if mode == "limit" else 10_000)
    assert {k: v for k, v in result.items() if k != "traceabilityValidation"} == {k: v for k, v in baseline.items() if k != "traceabilityValidation"}
    if mode == "limit":
        trace = result["traceabilityValidation"]
        assert statuses(trace) == dict.fromkeys(RULES, "no_evaluable")
        assert trace["traces"] == []


def test_client_cannot_replace_computed_changes_with_a_posted_list(transition_request):
    transition_request["changes"] = [{"entity": {"id": "fake"}, "operation": "created"}]
    assert report(transition_request)["traces"][0]["entity"]["id"] == "catalog"


def test_cli_uses_no_framework_database_or_provider():
    result = subprocess.run([sys.executable, "-B", "-S", "-m", "src.multimodel_validation.traceability", str(DEMO)], capture_output=True, text=True, check=True)
    assert statuses(json.loads(result.stdout)) == dict.fromkeys(RULES, "cumple")

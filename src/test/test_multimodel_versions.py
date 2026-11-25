import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.multimodel_validation.versions import validate_versions
from src.multimodel_validation.transition import validate_transition


DEMO = Path(__file__).parent / "fixtures" / "multimodel_versions.json"
RULES = ("RG17", "RG18", "RG19")


@pytest.fixture
def capture():
    return json.loads(DEMO.read_text(encoding="utf-8"))


def statuses(report):
    return {r["ruleId"]: r["status"] for r in report["rules"]}


def transition(capture):
    after = copy.deepcopy(capture)
    context = after.pop("versionContext")
    before = copy.deepcopy(after)
    before["snapshotId"] = "versions-before"
    return {"schemaVersion": 1, "before": before, "after": after, "versionContext": context}


def test_explicit_version_evidence_passes_without_mutating_input(capture):
    original = copy.deepcopy(capture)
    report = validate_versions(capture)
    assert statuses(report) == dict.fromkeys(RULES, "cumple")
    assert report["findings"] == []
    assert report["contextId"] == "ecommerce-version-evidence"
    assert report["versionCoverage"]["revisionCount"] == 3
    assert capture == original


def test_capture_ids_and_current_elements_do_not_invent_versions(capture):
    del capture["versionContext"]
    capture["snapshotId"] = "v123"
    report = validate_versions(capture)
    assert statuses(report) == dict.fromkeys(RULES, "no_evaluable")
    assert {f["code"] for f in report["findings"]} == {"missing_version_context"}


def test_empty_context_has_no_applicable_version_checks(capture):
    c = capture["versionContext"]
    for field in ("revisions", "compatibility", "correspondences", "changes", "historicalReferences"):
        c[field] = []
    assert statuses(validate_versions(capture)) == dict.fromkeys(RULES, "no_aplica")


def test_explicit_incompatible_version_pair_fails(capture):
    capture["versionContext"]["compatibility"][0]["compatible"] = False
    assert statuses(validate_versions(capture))["RG17"] == "incumple"


@pytest.mark.parametrize("problem", ["absent", "reverse", "duplicate"])
def test_compatibility_is_not_inferred_from_names_or_version_labels(capture, problem):
    c = capture["versionContext"]
    if problem == "absent": c["compatibility"] = []
    elif problem == "reverse":
        p = c["compatibility"][0]
        p["source"], p["target"] = p["target"], p["source"]
    else:
        p = copy.deepcopy(c["compatibility"][0]);p["id"] = "second-policy";c["compatibility"].append(p)
    assert statuses(validate_versions(capture))["RG17"] == "no_evaluable"


@pytest.mark.parametrize("scope,expected", [("complete", "incumple"), ("partial", "no_evaluable")])
def test_historical_missing_revision_respects_catalog_coverage(capture, scope, expected):
    c = capture["versionContext"]
    c["scope"] = scope
    c["historicalReferences"][0]["target"]["versionId"] = "not-provided"
    assert statuses(validate_versions(capture))["RG19"] == expected


def test_historical_lookup_never_substitutes_the_current_revision(capture):
    c = capture["versionContext"]
    c["revisions"][0]["model"]["elements"] = []
    c["changes"][0]["oldValue"] = None  # Creation of catalog at base-v2 is valid.
    assert statuses(validate_versions(capture))["RG19"] == "incumple"
    assert statuses(validate_versions(capture))["RG17"] == "cumple"


@pytest.mark.parametrize("problem", ["partial-revision", "duplicate-revision", "duplicate-element", "duplicate-relationship"])
def test_ambiguous_or_partial_historical_data_cannot_produce_false_passes(capture, problem):
    c = capture["versionContext"]
    rev = c["revisions"][0]
    if problem == "partial-revision": rev["scope"] = "partial"
    elif problem == "duplicate-revision": c["revisions"].append(copy.deepcopy(rev))
    elif problem == "duplicate-element": rev["model"]["elements"].append(copy.deepcopy(rev["model"]["elements"][0]))
    else:
        rev["model"]["relationships"] = [{"id": "r"}, {"id": "r"}]
    assert statuses(validate_versions(capture))["RG19"] == "no_evaluable"


@pytest.mark.parametrize("field", ["oldValue", "newValue"])
def test_history_values_must_match_the_exact_associated_revisions(capture, field):
    capture["versionContext"]["changes"][0][field]["name"] = "Wrong"
    result = validate_versions(capture)
    assert statuses(result)["RG18"] == "incumple"
    assert any(f["code"] == "history_value_mismatch" for f in result["findings"])


@pytest.mark.parametrize("problem", ["no-versions", "partial-object", "partial-scalar"])
def test_native_history_without_complete_version_metadata_is_unevaluable(capture, problem):
    event = capture["versionContext"]["changes"][0]
    if problem == "no-versions":
        del event["fromVersionId"];del event["toVersionId"]
    else:
        event.pop("valueScope")
        event["oldValue"] = {"name": "Catalogo"} if problem == "partial-object" else "Catalogo"
        event["newValue"] = {"name": "CatalogoWeb"} if problem == "partial-object" else "CatalogoWeb"
    result = statuses(validate_versions(capture))
    assert result == {"RG17": "cumple", "RG18": "no_evaluable", "RG19": "cumple"}


@pytest.mark.parametrize("scope,expected", [("complete", "incumple"), ("partial", "no_evaluable")])
def test_unrecorded_revision_change_distinguishes_absence_from_partial_history(capture, scope, expected):
    c = capture["versionContext"];c["historyScope"] = scope;c["changes"] = []
    assert statuses(validate_versions(capture))["RG18"] == expected


def test_lineage_cycle_is_detected_without_recursive_history_reconstruction(capture):
    c = capture["versionContext"]
    c["revisions"][0]["parentVersionIds"] = ["base-v2"]
    result = validate_versions(capture)
    assert statuses(result)["RG18"] == "incumple"
    assert any(f["code"] == "cyclic_revision_history" for f in result["findings"])


def test_merges_need_explicit_reconstruction_semantics(capture):
    c = capture["versionContext"]
    extra = copy.deepcopy(c["revisions"][0]);extra["versionId"] = "branch"
    c["revisions"].append(extra)
    c["revisions"][1]["parentVersionIds"].append("branch")
    assert statuses(validate_versions(capture))["RG18"] == "no_evaluable"


def test_missing_parent_is_not_replaced_by_a_revision_with_same_label_in_other_model(capture):
    c = capture["versionContext"]
    c["revisions"][1]["parentVersionIds"] = ["store-v1"]
    assert statuses(validate_versions(capture))["RG18"] == "incumple"


def test_multiple_events_for_same_entity_edge_need_an_intermediate_sequence(capture):
    c = capture["versionContext"]
    extra = copy.deepcopy(c["changes"][0]);extra["id"] = "another-change";c["changes"].append(extra)
    assert statuses(validate_versions(capture))["RG18"] == "no_evaluable"


def test_a_change_from_another_project_is_a_historical_integrity_failure(capture):
    capture["versionContext"]["changes"][0]["projectId"] = "foreign-project"
    assert statuses(validate_versions(capture))["RG18"] == "incumple"


def test_whole_model_event_covers_metadata_and_entity_changes(capture):
    c = capture["versionContext"]
    c["revisions"][1]["model"]["name"] = "New model name"
    event = c["changes"][0]
    event.update(entityType="model", entityId="base", oldValue=copy.deepcopy(c["revisions"][0]["model"]), newValue=copy.deepcopy(c["revisions"][1]["model"]))
    assert statuses(validate_versions(capture))["RG18"] == "cumple"


def test_reordering_existing_entities_needs_a_model_level_history_record(capture):
    c = capture["versionContext"]
    for rev in c["revisions"][:2]:
        rev["model"]["elements"].append({"id": "second", "type": "Feature", "name": "Other"})
    c["revisions"][1]["model"]["elements"].reverse()
    assert statuses(validate_versions(capture))["RG18"] == "incumple"
    c["changes"][0].update(entityType="model", entityId="base", oldValue=copy.deepcopy(c["revisions"][0]["model"]), newValue=copy.deepcopy(c["revisions"][1]["model"]))
    assert statuses(validate_versions(capture))["RG18"] == "cumple"


@pytest.mark.parametrize("problem", ["scalar-full-entity", "unsupported-entity"])
def test_uninterpretable_event_does_not_become_a_false_claim_of_absent_history(capture, problem):
    event = capture["versionContext"]["changes"][0]
    if problem == "scalar-full-entity":
        event["oldValue"], event["newValue"] = "Catalogo", "CatalogoWeb"
    else:
        event["entityType"] = "productLine"
    assert statuses(validate_versions(capture))["RG18"] == "no_evaluable"


def test_historical_relationship_reference_is_resolved_by_version_and_id(capture):
    c = capture["versionContext"]
    relationship = {"id": "self-link", "type": "Reference", "sourceId": "catalog", "targetId": "catalog"}
    for rev in c["revisions"][:2]:
        rev["model"]["relationships"] = [copy.deepcopy(relationship)]
    c["historicalReferences"][0]["target"].update(kind="relationship", id="self-link")
    assert statuses(validate_versions(capture))["RG19"] == "cumple"


def test_element_deletion_preserves_resolution_in_older_revision(capture):
    c = capture["versionContext"]
    c["revisions"][1]["model"]["elements"] = []
    c["changes"][0]["newValue"] = None
    c["correspondences"] = []
    result = statuses(validate_versions(capture))
    assert result["RG18"] == result["RG19"] == "cumple"


def test_model_tombstone_keeps_older_version_resolvable(capture):
    c = capture["versionContext"]
    c["revisions"][1]["model"] = None
    c["changes"][0].update(entityType="model", entityId="base", oldValue=copy.deepcopy(c["revisions"][0]["model"]), newValue=None)
    c["correspondences"] = []
    result = statuses(validate_versions(capture))
    assert result["RG18"] == result["RG19"] == "cumple"


def test_initial_model_creation_requires_explicit_null_from_version(capture):
    c = capture["versionContext"]
    c["changes"].append({"id": "initial-model", "projectId": "version-demo", "modelId": "base", "entityType": "model", "entityId": "base",
                         "fromVersionId": None, "toVersionId": "base-v1", "valueScope": "entity", "oldValue": None, "newValue": copy.deepcopy(c["revisions"][0]["model"])})
    assert statuses(validate_versions(capture))["RG18"] == "cumple"


@pytest.mark.parametrize("problem", ["version", "project", "extra", "missing-scope", "duplicate-id", "invalid-kind", "invalid-boolean", "model-identity", "duplicate-parent", "non-json", "too-many-revisions"])
def test_invalid_context_is_isolated_from_the_other_increments(capture, problem):
    c = capture["versionContext"]
    if problem == "version": c["schemaVersion"] = True
    elif problem == "project": c["projectId"] = "another-project"
    elif problem == "extra": c["execute"] = "ignored code must never run"
    elif problem == "missing-scope": del c["historyScope"]
    elif problem == "duplicate-id": c["historicalReferences"][0]["id"] = c["changes"][0]["id"]
    elif problem == "invalid-kind": c["correspondences"][0]["target"]["kind"] = "guess"
    elif problem == "invalid-boolean": c["compatibility"][0]["compatible"] = "true"
    elif problem == "model-identity": c["revisions"][0]["model"]["id"] = "other"
    elif problem == "duplicate-parent": c["revisions"][1]["parentVersionIds"] *= 2
    elif problem == "non-json": c["changes"][0]["oldValue"]["number"] = float("nan")
    else: c["revisions"] *= 44
    result = validate_versions(capture)
    assert statuses(result) == dict.fromkeys(RULES, "no_evaluable")
    assert {f["code"] for f in result["findings"]} == {"invalid_version_context"}


def test_budget_exhaustion_discards_partial_success_only_for_fifth_report(capture):
    result = validate_versions(capture, max_checks=5)
    assert statuses(result) == dict.fromkeys(RULES, "no_evaluable")
    assert {f["code"] for f in result["findings"]} == {"version_evaluation_limit"}


@pytest.mark.parametrize("mode", ["valid", "missing", "invalid", "incompatible", "limit"])
def test_existing_transition_reports_remain_identical(capture, mode):
    request = transition(capture)
    baseline = validate_transition({k: v for k, v in request.items() if k != "versionContext"})
    if mode == "missing": del request["versionContext"]
    elif mode == "invalid": request["versionContext"]["schemaVersion"] = 2
    elif mode == "incompatible": request["versionContext"]["compatibility"][0]["compatible"] = False
    result = validate_transition(request, max_version_checks=0 if mode == "limit" else 10_000)
    assert {k: v for k, v in result.items() if k != "versionValidation"} == {k: v for k, v in baseline.items() if k != "versionValidation"}


def test_cli_runs_without_database_framework_or_network():
    result = subprocess.run([sys.executable, "-B", "-S", "-m", "src.multimodel_validation.versions", str(DEMO)], capture_output=True, text=True, check=True)
    assert statuses(json.loads(result.stdout)) == dict.fromkeys(RULES, "cumple")

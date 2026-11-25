import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.multimodel_validation.transition import TransitionLimitExceeded, validate_transition
from src.multimodel_validation import validate_snapshot


DEMO = Path(__file__).parent / "fixtures" / "multimodel_transition.json"


def models(capture):
    line = capture["project"]["productLines"][0]
    return (line["domainEngineering"]["models"][0],
            line["applicationEngineering"]["applications"][0]["adaptations"][0]["models"][0])


@pytest.fixture
def transition():
    request = json.loads(DEMO.read_text(encoding="utf-8"))
    # The committed demo intentionally contains a deletion with surviving links.
    request["after"]["project"] = copy.deepcopy(request["before"]["project"])
    return request


def statuses(report):
    # These cases exercise the original impact/deletion subset independently.
    return {rule["ruleId"]: rule["status"] for rule in report["rules"] if rule["ruleId"] in {"RG13", "RG15"}}


def violations(report):
    return [finding for finding in report["findings"] if finding["status"] == "incumple" and "RG15" in finding["ruleIds"]]


def test_demo_reports_local_and_cross_model_surviving_references():
    request = json.loads(DEMO.read_text(encoding="utf-8"))
    report = validate_transition(request)
    assert statuses(report) == {"RG13": "cumple", "RG15": "incumple"}
    assert report["coverage"]["after"]["effectiveScope"] == "complete"
    assert len(report["changes"]) == 1
    assert report["changes"][0]["entity"] == {"kind": "element", "modelId": "base", "id": "catalog"}
    assert {finding["evidence"]["dependent"]["kind"] for finding in violations(report)} == {"element", "relationship"}
    assert len(violations(report)) == 2
    # A model-level dependency is potential impact, not a dangling element link.
    assert any(impact["field"] == "sourceModelIds" for impact in report["impacts"])
    assert all(finding["evidence"]["target"]["kind"] == "element" for finding in violations(report))


def test_unchanged_captures_have_no_applicable_transition_rules(transition):
    report = validate_transition(transition)
    assert statuses(report) == {"RG13": "no_aplica", "RG15": "no_aplica"}
    assert report["changes"] == report["impacts"] == report["findings"] == []


@pytest.mark.parametrize("field,value", [("name", "CatalogoPrueba"), ("type", "OtherType"), ("properties", [{"id": "price", "value": 12}])])
def test_edit_reports_direct_declared_dependents_without_semantic_inference(transition, field, value):
    base, _ = models(transition["after"])
    base["elements"][1][field] = value
    report = validate_transition(transition)
    assert statuses(report) == {"RG13": "cumple", "RG15": "no_aplica"}
    change, = report["changes"]
    assert change["operation"] == "updated"
    assert change["changedFields"] == [field]
    assert {impact["dependent"]["modelId"] for impact in report["impacts"]} == {"base", "derived"}
    assert any(impact["referencePath"].endswith("/sourceModelElements/0") for impact in report["impacts"])


def test_deleting_element_and_cleaning_references_together_passes(transition):
    base, derived = models(transition["after"])
    base["elements"].pop()
    base["relationships"].clear()
    derived["elements"][0]["sourceModelElements"].clear()
    report = validate_transition(transition)
    assert statuses(report) == {"RG13": "cumple", "RG15": "cumple"}
    assert not [finding for finding in report["findings"] if set(finding["ruleIds"]) & {"RG13", "RG15"}]
    assert any(impact["phase"] == "before" and impact["field"] == "sourceModelElements" for impact in report["impacts"])


def test_deleting_parent_leaves_detectable_parent_id(transition):
    base, _ = models(transition["after"])
    base["elements"].pop(0)
    report = validate_transition(transition)
    assert statuses(report)["RG15"] == "incumple"
    assert any(finding["path"].endswith("/parentId") for finding in violations(report))


def test_new_reference_to_a_deleted_element_also_fails(transition):
    base, derived = models(transition["after"])
    base["elements"].pop()
    base["relationships"].clear()
    derived["elements"].append({"id": "new", "sourceModelElements": [{"modelId": "base", "elementId": "catalog"}]})
    report = validate_transition(transition)
    assert len(violations(report)) == 2
    assert any(finding["evidence"]["dependent"]["id"] == "new" for finding in violations(report))


def test_model_deletion_covers_children_once(transition):
    transition["after"]["project"]["productLines"][0]["domainEngineering"]["models"].clear()
    report = validate_transition(transition)
    change, = report["changes"]
    assert change["entity"]["kind"] == "model"
    assert change["operation"] == "deleted"
    assert statuses(report)["RG15"] == "incumple"
    assert {finding["evidence"]["target"]["kind"] for finding in violations(report)} == {"model", "element"}


def test_empty_model_deletion_is_checked(transition):
    for phase in ("before", "after"):
        base, derived = models(transition[phase])
        base["elements"].clear()
        base["relationships"].clear()
        derived["elements"][0]["sourceModelElements"].clear()
    transition["after"]["project"]["productLines"][0]["domainEngineering"]["models"].clear()
    assert len(violations(validate_transition(transition))) == 1


def test_preexisting_dangling_reference_is_not_attributed_to_new_deletion(transition):
    for phase in ("before", "after"):
        _, derived = models(transition[phase])
        derived["elements"][0]["sourceModelElements"] = [{"modelId": "base", "elementId": "already-missing"}]
    base, _ = models(transition["after"])
    base["elements"].pop()
    base["relationships"].clear()
    report = validate_transition(transition)
    assert statuses(report)["RG15"] == "cumple"
    assert not violations(report)
    assert any(rule["status"] == "incumple" for rule in validate_snapshot(transition["after"])["rules"])


def test_model_deletion_does_not_reclassify_preexisting_missing_element(transition):
    for phase in ("before", "after"):
        _, derived = models(transition[phase])
        derived["sourceModelIds"].clear()
        derived["elements"][0]["sourceModelElements"] = [{"modelId": "base", "elementId": "already-missing"}]
    transition["after"]["project"]["productLines"][0]["domainEngineering"]["models"].clear()
    report = validate_transition(transition)
    assert statuses(report)["RG15"] == "cumple"
    assert not violations(report)


@pytest.mark.parametrize("phase", ["before", "after"])
def test_partial_capture_reports_uncertainty_and_does_not_infer_absence(transition, phase):
    transition[phase]["scope"] = "partial"
    base, _ = models(transition["after"])
    base["elements"].pop()
    base["relationships"].clear()
    _, derived = models(transition["after"])
    derived["elements"][0]["sourceModelElements"].clear()
    report = validate_transition(transition)
    assert statuses(report) == {"RG13": "no_evaluable", "RG15": "no_evaluable"}
    if phase == "after":
        assert not any(change["operation"] == "deleted" for change in report["changes"])


def test_omitted_branch_downgrades_even_declared_complete_capture(transition):
    del transition["after"]["project"]["productLines"][0]["domainEngineering"]
    report = validate_transition(transition)
    assert report["coverage"]["after"]["effectiveScope"] == "partial"
    assert not any(change["operation"] == "deleted" for change in report["changes"])
    assert statuses(report)["RG15"] == "no_evaluable"


@pytest.mark.parametrize("kind", ["model", "element", "relationship"])
def test_duplicate_id_does_not_become_a_false_deletion(transition, kind):
    base, _ = models(transition["after"])
    if kind == "model":
        transition["after"]["project"]["productLines"][0]["scope"]["models"].append(copy.deepcopy(base))
    else:
        field = "elements" if kind == "element" else "relationships"
        base[field].append(copy.deepcopy(base[field][0]))
    report = validate_transition(transition)
    assert statuses(report) == {"RG13": "no_evaluable", "RG15": "no_evaluable"}
    assert not any(change["operation"] == "deleted" for change in report["changes"])
    assert any(finding["code"] == "ambiguous_identity" for finding in report["findings"])


@pytest.mark.parametrize("field", ["elements", "relationships"])
def test_missing_record_id_is_not_a_confirmed_deletion(transition, field):
    base, _ = models(transition["after"])
    del base[field][0]["id"]
    report = validate_transition(transition)
    assert statuses(report)["RG15"] == "no_evaluable"
    assert report["coverage"]["after"]["effectiveScope"] == "partial"
    assert not violations(report)


@pytest.mark.parametrize("value", [None, "wrong", [{"modelId": "base", "elementId": "catalog", "version": "old"}]])
def test_unknown_origin_format_is_not_interpreted_as_a_historical_link(transition, value):
    _, derived = models(transition["after"])
    derived["elements"][0]["sourceModelElements"] = value
    report = validate_transition(transition)
    assert statuses(report)["RG13"] == "no_evaluable"
    assert any(finding["code"] == "unsupported_reference" for finding in report["findings"])


def test_no_correspondence_is_inferred_from_names(transition):
    for phase in ("before", "after"):
        _, derived = models(transition[phase])
        derived["sourceModelIds"].clear()
        derived["elements"][0]["sourceModelElements"].clear()
    base, _ = models(transition["after"])
    base["elements"][1]["name"] = "CatalogoWeb"
    report = validate_transition(transition)
    assert not any(impact["dependent"]["modelId"] == "derived" for impact in report["impacts"])


def test_collection_order_does_not_change_entity_identity(transition):
    base, _ = models(transition["after"])
    base["elements"].reverse()
    line = transition["after"]["project"]["productLines"][0]
    line["scope"]["models"].append(line["domainEngineering"]["models"].pop())
    report = validate_transition(transition)
    assert report["changes"] == []


def test_creation_of_a_model_is_reported_once(transition):
    new_model = {"id": "new", "elements": [{"id": "same-id"}], "relationships": []}
    transition["after"]["project"]["productLines"][0]["scope"]["models"].append(new_model)
    report = validate_transition(transition)
    change, = report["changes"]
    assert change["operation"] == "created" and change["entity"]["kind"] == "model"
    assert statuses(report)["RG15"] == "no_aplica"


def test_relationship_edit_tracks_model_dependents(transition):
    base, _ = models(transition["after"])
    base["relationships"][0]["targetId"] = "root"
    report = validate_transition(transition)
    change, = report["changes"]
    assert change["entity"]["kind"] == "relationship"
    assert any(impact["field"] == "sourceModelIds" for impact in report["impacts"])


def test_missing_fields_in_partial_records_are_not_reported_as_edits(transition):
    transition["after"]["scope"] = "partial"
    base, _ = models(transition["after"])
    del base["elements"][1]["name"]
    report = validate_transition(transition)
    assert report["changes"] == []
    assert statuses(report)["RG13"] == "no_evaluable"


def test_partial_capture_can_still_show_a_known_update_without_claiming_complete_impact(transition):
    transition["after"]["scope"] = "partial"
    base, _ = models(transition["after"])
    base["elements"][1]["name"] = "KnownEdit"
    report = validate_transition(transition)
    assert report["changes"][0]["changedFields"] == ["name"]
    assert report["impacts"]
    assert statuses(report)["RG13"] == "no_evaluable"


@pytest.mark.parametrize("previous,current", [(False, 0), (True, 1), (1, 1.0), (1, "1")])
def test_property_value_type_changes_are_not_hidden_by_python_equality(transition, previous, current):
    for phase, value in (("before", previous), ("after", current)):
        base, _ = models(transition[phase])
        base["elements"][1]["properties"] = [{"name": "Value", "value": value}]
    report = validate_transition(transition)
    assert report["changes"][0]["changedFields"] == ["properties"]


def test_impact_does_not_claim_transitive_semantic_propagation(transition):
    third = {"id": "third", "sourceModelIds": ["derived"], "elements": [
        {"id": "catalog", "sourceModelElements": [{"modelId": "derived", "elementId": "catalog"}]}
    ], "relationships": []}
    for phase in ("before", "after"):
        transition[phase]["project"]["productLines"][0]["scope"]["models"].append(copy.deepcopy(third))
    base, _ = models(transition["after"])
    base["elements"][1]["name"] = "Changed"
    report = validate_transition(transition)
    assert report["impactDepth"] == 1
    assert any(impact["dependent"]["modelId"] == "derived" for impact in report["impacts"])
    assert not any(impact["dependent"]["modelId"] == "third" for impact in report["impacts"])


def test_deleted_reference_owner_is_not_a_surviving_violation(transition):
    base, derived = models(transition["after"])
    base["elements"].pop()
    base["relationships"].clear()
    derived["elements"].clear()
    report = validate_transition(transition)
    assert statuses(report)["RG15"] == "cumple"
    assert not violations(report)


def test_removed_model_and_its_correspondences_pass_together(transition):
    _, derived = models(transition["after"])
    derived["sourceModelIds"].clear()
    derived["elements"][0]["sourceModelElements"].clear()
    transition["after"]["project"]["productLines"][0]["domainEngineering"]["models"].clear()
    report = validate_transition(transition)
    assert statuses(report)["RG15"] == "cumple"
    assert not violations(report)


def test_input_is_unchanged_and_output_is_deterministic(transition):
    base, _ = models(transition["after"])
    base["elements"][1]["name"] = "Edited"
    saved = copy.deepcopy(transition)
    first = validate_transition(transition)
    first["changes"][0]["entity"]["id"] = "mutated-report"
    second = validate_transition(transition)
    assert transition == saved
    assert second == validate_transition(transition)
    assert second["changes"][0]["entity"]["id"] == "catalog"


@pytest.mark.parametrize("error", ["schema", "boolean-schema", "missing-after", "other-project", "same-snapshot", "embedded-id", "bad-scope", "bad-id"])
def test_invalid_contract_fails_explicitly(transition, error):
    if error == "schema":
        transition["schemaVersion"] = 2
    elif error == "boolean-schema":
        transition["before"]["schemaVersion"] = True
    elif error == "missing-after":
        del transition["after"]
    elif error == "other-project":
        transition["after"]["projectId"] = "other"
        transition["after"]["project"]["id"] = "other"
    elif error == "same-snapshot":
        transition["after"]["snapshotId"] = transition["before"]["snapshotId"]
    elif error == "embedded-id":
        transition["after"]["project"]["id"] = "other"
    elif error == "bad-scope":
        transition["after"]["scope"] = "unknown"
    else:
        transition["before"]["projectId"] = " "
    with pytest.raises(ValueError):
        validate_transition(transition)


def test_cli_uses_only_standard_library_and_preserves_fixture():
    original = DEMO.read_bytes()
    process = subprocess.run(
        [sys.executable, "-B", "-S", "-m", "src.multimodel_validation.transition", str(DEMO)],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True,
    )
    assert statuses(json.loads(process.stdout))["RG15"] == "incumple"
    assert DEMO.read_bytes() == original


def test_impact_limit_preserves_the_input_and_does_not_return_a_truncated_success():
    request = json.loads(DEMO.read_text(encoding="utf-8"))
    saved = copy.deepcopy(request)
    with pytest.raises(TransitionLimitExceeded):
        validate_transition(request, max_impacts=5)
    assert request == saved
    assert len(validate_transition(request, max_impacts=6)["impacts"]) == 6

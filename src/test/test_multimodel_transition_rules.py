import copy
import json
from pathlib import Path

import pytest

from src.multimodel_validation.transition import validate_transition


DEMO = Path(__file__).parent / "fixtures" / "multimodel_transition.json"


@pytest.fixture
def transition():
    request = json.loads(DEMO.read_text(encoding="utf-8"))
    request["after"]["project"] = copy.deepcopy(request["before"]["project"])
    return request


def records(request, phase="after"):
    line = request[phase]["project"]["productLines"][0]
    base = line["domainEngineering"]["models"][0]
    derived = line["applicationEngineering"]["applications"][0]["adaptations"][0]["models"][0]
    return base, derived


def statuses(report):
    return {rule["ruleId"]: rule["status"] for rule in report["rules"]}


def test_all_four_rules_are_reported_and_an_unchanged_state_is_not_a_change(transition):
    result = validate_transition(transition)
    assert statuses(result) == dict.fromkeys(("RG13", "RG14", "RG15", "RG16"), "no_aplica")
    assert result["correspondenceReviews"] == []


def test_rename_queues_semantic_review_without_claiming_a_contradiction(transition):
    base, _ = records(transition)
    base["elements"][1]["name"] = "CatalogoPrueba"
    result = validate_transition(transition)
    assert statuses(result) == {"RG13": "cumple", "RG14": "no_evaluable", "RG15": "no_aplica", "RG16": "cumple"}
    assert {review["field"] for review in result["correspondenceReviews"]} == {"sourceModelIds", "sourceModelElements"}
    assert all(review["code"] == "correspondence_review_required" for review in result["correspondenceReviews"])
    assert result["structuralValidation"]["delta"] == {"introduced": 0, "persisting": 0, "resolved": 0, "unclassified": 0}


@pytest.mark.parametrize("owner", ["base-model", "derived-element"])
def test_changes_to_either_correspondence_endpoint_require_review(transition, owner):
    base, derived = records(transition)
    if owner == "base-model":
        base["name"] = "Modified base context"
    else:
        derived["elements"][0]["name"] = "Modified derived content"
    result = validate_transition(transition)
    assert statuses(result)["RG14"] == "no_evaluable"
    assert any(review["field"] == "sourceModelElements" for review in result["correspondenceReviews"])


def test_new_or_retargeted_correspondence_is_reviewed_even_when_its_ids_resolve(transition):
    _, derived = records(transition)
    derived["elements"][0]["sourceModelElements"][0]["elementId"] = "root"
    result = validate_transition(transition)
    assert statuses(result)["RG14"] == "no_evaluable"
    assert any(review["code"] == "correspondence_review_required" and review["target"]["id"] == "root"
               for review in result["correspondenceReviews"])
    assert statuses(result)["RG16"] == "cumple"


def test_invalid_correspondence_fails_rg14_and_rg16(transition):
    _, derived = records(transition)
    derived["elements"][0]["sourceModelElements"][0]["elementId"] = "missing"
    result = validate_transition(transition)
    assert statuses(result)["RG14"] == statuses(result)["RG16"] == "incumple"
    assert result["structuralValidation"]["delta"]["introduced"] == 1


def test_the_same_deleted_correspondence_is_one_finding_for_rg14_and_rg15():
    request = json.loads(DEMO.read_text(encoding="utf-8"))
    report = validate_transition(request)
    shared = [finding for finding in report["findings"] if set(finding["ruleIds"]) == {"RG14", "RG15"}]
    assert len(shared) == 1
    path = shared[0]["path"]
    assert len([finding for finding in report["findings"] if finding["path"] == path and finding["status"] == "incumple"]) == 1


def test_explicit_invalid_id_in_changed_correspondence_is_not_only_a_pending_review(transition):
    _, derived = records(transition)
    derived["elements"][0]["sourceModelElements"][0]["elementId"] = " "
    result = validate_transition(transition)
    assert statuses(result)["RG14"] == "incumple"
    assert any(finding["code"] == "invalid_affected_correspondence" for finding in result["findings"])


def test_removing_correspondences_is_structurally_checked_without_a_pending_active_link(transition):
    _, derived = records(transition)
    derived["sourceModelIds"].clear()
    derived["elements"][0]["sourceModelElements"].clear()
    result = validate_transition(transition)
    assert statuses(result)["RG14"] == "cumple"
    assert all(review["code"] == "correspondence_removed" for review in result["correspondenceReviews"])
    assert statuses(result)["RG16"] == "cumple"


@pytest.mark.parametrize("field", ["x", "y", "width", "height", "points"])
def test_presentation_changes_do_not_claim_semantic_review_is_needed(transition, field):
    base, _ = records(transition)
    base["elements"][1][field] = [] if field == "points" else 123
    result = validate_transition(transition)
    assert statuses(result)["RG14"] == "no_aplica"
    assert result["correspondenceReviews"] == []
    assert statuses(result)["RG16"] == "cumple"


def test_rg16_uses_an_explicit_metamodel_to_detect_a_new_type_violation(transition):
    base, _ = records(transition)
    base["elements"][1]["type"] = "Container"
    result = validate_transition(transition)
    assert statuses(result)["RG16"] == "incumple"
    after_rules = {rule["ruleId"]: rule["status"] for rule in result["structuralValidation"]["after"]["rules"]}
    assert after_rules["RG04"] == "incumple"
    assert result["structuralValidation"]["delta"]["introduced"] == 1


def test_rg16_does_not_assume_a_missing_metamodel_is_valid(transition):
    for phase in ("before", "after"):
        del transition[phase]["metamodels"]
    base, _ = records(transition)
    base["elements"][1]["name"] = "Changed"
    result = validate_transition(transition)
    assert statuses(result)["RG16"] == "no_evaluable"
    finding = next(finding for finding in result["findings"] if finding["code"] == "post_change_structural_validation")
    assert finding["evidence"]["unevaluableRuleIds"] == ["RG04"]


def test_rg16_cannot_be_disabled_by_narrowing_rules_inside_a_capture(transition):
    transition["after"]["ruleIds"] = []
    base, _ = records(transition)
    base["elements"].append(copy.deepcopy(base["elements"][0]))
    result = validate_transition(transition)
    assert statuses(result)["RG16"] == "incumple"
    assert {rule["ruleId"] for rule in result["structuralValidation"]["after"]["rules"]} == {"RG01", "RG02", "RG03", "RG04", "RG05"}


def test_preexisting_violation_persists_without_being_attributed_to_the_edit(transition):
    for phase in ("before", "after"):
        _, derived = records(transition, phase)
        derived["elements"][0]["sourceModelElements"][0]["elementId"] = "already-missing"
        transition[phase]["project"]["productLines"][0]["scope"]["models"].append({
            "id": "unrelated", "elements": [{"id": "other", "name": "Before"}], "relationships": [],
        })
    transition["after"]["project"]["productLines"][0]["scope"]["models"][0]["elements"][0]["name"] = "After"
    result = validate_transition(transition)
    assert statuses(result)["RG14"] == "no_aplica"
    assert statuses(result)["RG16"] == "incumple"
    assert result["structuralValidation"]["delta"] == {"introduced": 0, "persisting": 1, "resolved": 0, "unclassified": 0}


def test_repaired_reference_is_counted_as_resolved(transition):
    _, derived = records(transition, "before")
    derived["elements"][0]["sourceModelElements"][0]["elementId"] = "missing-before"
    result = validate_transition(transition)
    assert statuses(result)["RG16"] == "cumple"
    assert result["structuralValidation"]["delta"]["resolved"] == 1


def test_array_reordering_does_not_create_new_errors_or_fake_resolutions(transition):
    for phase in ("before", "after"):
        base, _ = records(transition, phase)
        base["relationships"].extend([
            {"id": "bad-1", "type": "Contains", "sourceId": "root", "targetId": "missing-1"},
            {"id": "bad-2", "type": "Contains", "sourceId": "root", "targetId": "missing-2"},
        ])
    base, _ = records(transition)
    base["relationships"].reverse()
    base["elements"][0]["name"] = "Updated"
    delta = validate_transition(transition)["structuralValidation"]["delta"]
    assert delta == {"introduced": 0, "persisting": 2, "resolved": 0, "unclassified": 0}


def test_partial_baseline_does_not_classify_a_failure_as_new(transition):
    transition["before"]["scope"] = "partial"
    _, derived = records(transition)
    derived["elements"][0]["sourceModelElements"][0]["elementId"] = "missing"
    result = validate_transition(transition)
    assert statuses(result)["RG16"] == "incumple"
    assert result["structuralValidation"]["delta"]["introduced"] == 0
    assert result["structuralValidation"]["delta"]["unclassified"] == 1


def test_a_partial_baseline_does_not_hide_a_complete_and_valid_post_state(transition):
    transition["before"]["scope"] = "partial"
    base, _ = records(transition)
    base["elements"][1]["name"] = "Known edit"
    result = validate_transition(transition)
    assert statuses(result)["RG13"] == "no_evaluable"
    assert statuses(result)["RG16"] == "cumple"
    assert result["structuralValidation"]["after"]["coverage"]["effectiveScope"] == "complete"


def test_partial_nested_omission_is_explicitly_uncertain_instead_of_a_confirmed_edit(transition):
    for phase in ("before", "after"):
        base, _ = records(transition, phase)
        base["elements"][1]["properties"] = [{"name": "Price", "value": 10, "comment": "captured"}]
    transition["after"]["scope"] = "partial"
    base, _ = records(transition)
    del base["elements"][1]["properties"][0]["comment"]
    result = validate_transition(transition)
    change, = result["changes"]
    assert change["changedFields"] == []
    assert change["uncertainFields"] == ["properties"]
    assert statuses(result)["RG16"] == "no_evaluable"


def test_the_completed_increment_is_deterministic_and_keeps_inputs_detached(transition):
    base, _ = records(transition)
    base["elements"][1]["name"] = "Edited"
    saved = copy.deepcopy(transition)
    first = validate_transition(transition)
    assert first == validate_transition(transition)
    assert transition == saved
    first["structuralValidation"]["after"]["findings"].append({"changed": True})
    assert transition == saved


@pytest.mark.parametrize("metamodels", [None, [], {" ": {}}])
def test_invalid_metamodel_contract_is_rejected(transition, metamodels):
    transition["after"]["metamodels"] = metamodels
    with pytest.raises(ValueError):
        validate_transition(transition)

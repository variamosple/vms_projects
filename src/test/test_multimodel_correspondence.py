import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.multimodel_validation.correspondence import validate_correspondences
from src.multimodel_validation.transition import validate_transition


DEMO = Path(__file__).parent / "fixtures" / "multimodel_correspondence.json"


@pytest.fixture
def capture():
    return json.loads(DEMO.read_text(encoding="utf-8"))


def models(capture):
    line = capture["project"]["productLines"][0]
    return line["domainEngineering"]["models"][0], line["applicationEngineering"]["applications"][0]["adaptations"][0]["models"][0]


def statuses(report):
    return {rule["ruleId"]: rule["status"] for rule in report["rules"]}


def test_explicit_cross_language_criteria_pass_with_different_names(capture):
    report = validate_correspondences(capture)
    assert statuses(report) == {"RG06": "cumple", "RG07": "cumple", "RG08": "cumple"}
    assert report["findings"] == []
    assert report["profileId"] == "ecommerce-explicit-criteria"
    assert len(report["coverage"]["models"]) == 2


def test_absent_criteria_are_not_invented(capture):
    del capture["correspondenceProfile"]
    result = validate_correspondences(capture)
    assert statuses(result) == dict.fromkeys(("RG06", "RG07", "RG08"), "no_evaluable")
    assert {finding["code"] for finding in result["findings"]} == {"missing_correspondence_profile"}


@pytest.mark.parametrize("scope", ["complete", "partial"])
def test_explicit_empty_policy_has_no_applicable_checks(capture, scope):
    capture["scope"] = scope
    profile = capture["correspondenceProfile"]
    for field in ("correspondences", "dependencies", "requiredCorrespondences"):
        profile[field] = []
    assert statuses(validate_correspondences(capture)) == dict.fromkeys(("RG06", "RG07", "RG08"), "no_aplica")


@pytest.mark.parametrize("source,target", [(False, 0), (True, 1), (1, "1"), ("different", "CAT-001")])
def test_mismatches_use_typed_values_and_explicit_criteria(capture, source, target):
    base, derived = models(capture)
    derived["elements"][0]["properties"][0]["value"] = source
    base["elements"][0]["properties"][0]["value"] = target
    assert statuses(validate_correspondences(capture))["RG06"] == "incumple"


def test_not_equals_operator_and_null_are_supported_without_expressions(capture):
    base, derived = models(capture)
    derived["elements"][0]["properties"][0]["value"] = None
    capture["correspondenceProfile"]["correspondences"][0]["checks"][0]["operator"] = "not_equals"
    assert statuses(validate_correspondences(capture))["RG06"] == "cumple"
    base["elements"][0]["properties"][0]["value"] = None
    assert statuses(validate_correspondences(capture))["RG06"] == "incumple"


@pytest.mark.parametrize("problem", ["missing", "duplicate", "malformed"])
def test_missing_or_ambiguous_attributes_are_unevaluable(capture, problem):
    _, derived = models(capture)
    properties = derived["elements"][0]["properties"]
    if problem == "missing":
        properties.pop(0)
    elif problem == "duplicate":
        properties.append(copy.deepcopy(properties[0]))
    else:
        properties.append(None)
    assert statuses(validate_correspondences(capture))["RG06"] == "no_evaluable"


def test_dictionary_paths_allow_another_attribute_structure(capture):
    base, derived = models(capture)
    base["elements"][0]["attributes"] = {"key": "same"}
    derived["elements"][0]["custom"] = {"key": "same"}
    check = capture["correspondenceProfile"]["correspondences"][0]["checks"][0]
    check.update(source={"path": ["custom", "key"]}, target={"path": ["attributes", "key"]})
    assert statuses(validate_correspondences(capture))["RG06"] == "cumple"


def test_strings_are_literal_values_and_not_expressions(capture):
    base, derived = models(capture)
    base["elements"][0]["properties"][0]["value"] = "2 + 2"
    derived["elements"][0]["properties"][0]["value"] = "2 + 2"
    assert statuses(validate_correspondences(capture))["RG06"] == "cumple"
    base["elements"][0]["properties"][0]["value"] = 4
    assert statuses(validate_correspondences(capture))["RG06"] == "incumple"


def test_model_level_correspondences_are_explicitly_selected(capture):
    base, derived = models(capture)
    base["businessKey"] = derived["businessKey"] = "stores"
    capture["correspondenceProfile"]["correspondences"] = [{
        "id": "model-key", "level": "model", "source": {"modelId": "derived"}, "target": {"modelId": "base"},
        "checks": [{"source": {"path": ["businessKey"]}, "target": {"path": ["businessKey"]}, "operator": "equals"}],
    }]
    assert statuses(validate_correspondences(capture))["RG06"] == "cumple"


def test_language_selectors_support_numeric_and_string_ids(capture):
    base, derived = models(capture)
    base["languageId"], derived["languageId"] = 7, "8"
    policy = capture["correspondenceProfile"]["correspondences"][0]
    policy["source"] = {"languageId": 8, "elementType": "Variant"}
    policy["target"] = {"languageId": "7", "elementType": "Item"}
    assert statuses(validate_correspondences(capture))["RG06"] == "cumple"


def test_no_policy_applies_to_an_explicitly_different_target_type(capture):
    capture["correspondenceProfile"]["correspondences"][0]["target"]["elementType"] = "Other"
    assert statuses(validate_correspondences(capture))["RG06"] == "no_aplica"


def test_unrelated_foreign_reference_does_not_pollute_explicit_model_criteria(capture):
    _, derived = models(capture)
    derived["elements"][0]["sourceModelElements"].append({"modelId": "outside-this-policy", "elementId": "missing"})
    report = validate_correspondences(capture)
    assert statuses(report)["RG06"] == statuses(report)["RG08"] == "cumple"


def test_active_dependency_must_respect_its_condition(capture):
    base, _ = models(capture)
    base["elements"][0]["properties"][1]["value"] = False
    assert statuses(validate_correspondences(capture))["RG07"] == "incumple"


def test_inactive_dependency_does_not_require_an_unavailable_target(capture):
    _, derived = models(capture)
    derived["elements"][0]["properties"][1]["value"] = False
    capture["correspondenceProfile"]["dependencies"][0]["target"]["elementId"] = "missing"
    assert statuses(validate_correspondences(capture))["RG07"] == "no_aplica"


@pytest.mark.parametrize("reverse", [False, True])
def test_a_false_guard_is_decisive_even_if_another_guard_is_unknown(capture, reverse):
    _, derived = models(capture)
    derived["elements"][0]["properties"][1]["value"] = False
    guards = capture["correspondenceProfile"]["dependencies"][0]["when"]
    guards.append({"attribute": {"property": "Unknown"}, "operator": "equals", "value": True})
    if reverse:
        guards.reverse()
    assert statuses(validate_correspondences(capture))["RG07"] == "no_aplica"


def test_unknown_activation_cannot_be_treated_as_false_or_true(capture):
    _, derived = models(capture)
    derived["elements"][0]["properties"].pop()
    assert statuses(validate_correspondences(capture))["RG07"] == "no_evaluable"


@pytest.mark.parametrize("scope,expected", [("complete", "incumple"), ("partial", "no_evaluable")])
def test_dependency_target_absence_obeys_coverage(capture, scope, expected):
    capture["scope"] = scope
    capture["correspondenceProfile"]["dependencies"][0]["target"]["elementId"] = "missing"
    assert statuses(validate_correspondences(capture))["RG07"] == expected


@pytest.mark.parametrize("scope,expected", [("complete", "incumple"), ("partial", "no_evaluable")])
def test_absent_required_links_are_not_false_orphans_under_partial_coverage(capture, scope, expected):
    capture["scope"] = scope
    _, derived = models(capture)
    derived["elements"][0]["sourceModelElements"] = []
    report = validate_correspondences(capture)
    assert statuses(report)["RG07"] == expected
    assert statuses(report)["RG08"] == expected


def test_legacy_missing_origin_collection_is_unknown_not_an_orphan(capture):
    _, derived = models(capture)
    del derived["elements"][0]["sourceModelElements"]
    report = validate_correspondences(capture)
    assert statuses(report)["RG08"] == "no_evaluable"
    assert not any(finding["status"] == "incumple" and "RG08" in finding["ruleIds"] for finding in report["findings"])


def test_duplicate_links_do_not_count_as_two_distinct_correspondences(capture):
    _, derived = models(capture)
    origins = derived["elements"][0]["sourceModelElements"]
    origins.append(copy.deepcopy(origins[0]))
    policy = capture["correspondenceProfile"]["requiredCorrespondences"][0]
    policy.update(minimum=2, maximum=2)
    assert statuses(validate_correspondences(capture))["RG08"] == "incumple"


@pytest.mark.parametrize("scope", ["complete", "partial"])
def test_known_maximum_violation_is_reported_even_with_partial_coverage(capture, scope):
    capture["scope"] = scope
    base, derived = models(capture)
    extra = copy.deepcopy(base["elements"][0])
    extra["id"] = "second"
    base["elements"].append(extra)
    derived["elements"][0]["sourceModelElements"].append({"modelId": "base", "elementId": "second"})
    assert statuses(validate_correspondences(capture))["RG08"] == "incumple"


@pytest.mark.parametrize("kind", ["model", "element"])
def test_duplicate_identity_prevents_semantic_judgments(capture, kind):
    base, _ = models(capture)
    if kind == "model":
        capture["project"]["productLines"][0]["scope"]["models"].append(copy.deepcopy(base))
    else:
        base["elements"].append(copy.deepcopy(base["elements"][0]))
    assert statuses(validate_correspondences(capture)) == dict.fromkeys(("RG06", "RG07", "RG08"), "no_evaluable")


@pytest.mark.parametrize("place", ["scope", "applicationEngineering", "application", "adaptation"])
def test_source_and_target_models_can_be_in_all_supported_locations(capture, place):
    line = capture["project"]["productLines"][0]
    base = line["domainEngineering"]["models"].pop()
    application = line["applicationEngineering"]["applications"][0]
    destination = line[place] if place in ("scope", "applicationEngineering") else application if place == "application" else application["adaptations"][0]
    destination["models"].append(base)
    assert statuses(validate_correspondences(capture)) == dict.fromkeys(("RG06", "RG07", "RG08"), "cumple")


@pytest.mark.parametrize("error", ["version", "unknown-field", "duplicate-id", "operator", "attribute", "bounds", "same-model-dependency", "missing-category"])
def test_invalid_criteria_are_reported_without_fake_success(capture, error):
    profile = capture["correspondenceProfile"]
    if error == "version":
        profile["schemaVersion"] = True
    elif error == "unknown-field":
        profile["script"] = "unsupported"
    elif error == "duplicate-id":
        profile["dependencies"][0]["id"] = profile["correspondences"][0]["id"]
    elif error == "operator":
        profile["correspondences"][0]["checks"][0]["operator"] = "eval"
    elif error == "attribute":
        profile["correspondences"][0]["checks"][0]["source"] = {"path": []}
    elif error == "bounds":
        profile["requiredCorrespondences"][0]["maximum"] = 0
    elif error == "same-model-dependency":
        profile["dependencies"][0]["target"]["modelId"] = "derived"
    else:
        del profile["requiredCorrespondences"]
    report = validate_correspondences(capture)
    assert statuses(report) == dict.fromkeys(("RG06", "RG07", "RG08"), "no_evaluable")
    assert {finding["code"] for finding in report["findings"]} == {"invalid_correspondence_profile"}


def test_work_budget_does_not_expose_a_partial_success(capture):
    report = validate_correspondences(capture, max_checks=1)
    assert statuses(report) == dict.fromkeys(("RG06", "RG07", "RG08"), "no_evaluable")
    assert {finding["code"] for finding in report["findings"]} == {"correspondence_evaluation_limit"}


def test_determinism_input_immutability_and_detached_diagnostics(capture):
    _, derived = models(capture)
    derived["elements"][0]["properties"][0]["value"] = "Mismatch"
    saved = copy.deepcopy(capture)
    report = validate_correspondences(capture)
    assert report == validate_correspondences(capture)
    finding = next(finding for finding in report["findings"] if "sourceAttribute" in finding["evidence"])
    finding["evidence"]["sourceAttribute"]["property"] = "Changed output"
    assert capture == saved


def test_third_increment_cannot_change_the_first_two_results(capture):
    after = {key: value for key, value in capture.items() if key != "correspondenceProfile"}
    before = copy.deepcopy(after)
    before["snapshotId"] = "before"
    request = {"schemaVersion": 1, "before": before, "after": after}
    without = validate_transition(request)
    request["correspondenceProfile"] = capture["correspondenceProfile"]
    with_criteria = validate_transition(request)
    assert statuses(with_criteria["correspondenceValidation"]) == dict.fromkeys(("RG06", "RG07", "RG08"), "cumple")
    assert {key: value for key, value in with_criteria.items() if key != "correspondenceValidation"} == {
        key: value for key, value in without.items() if key != "correspondenceValidation"
    }


def test_cli_is_standard_library_only_and_example_matches_the_fixture(capture):
    original = DEMO.read_bytes()
    completed = subprocess.run([sys.executable, "-B", "-S", "-m", "src.multimodel_validation.correspondence", str(DEMO)],
                               cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, check=True)
    assert statuses(json.loads(completed.stdout)) == dict.fromkeys(("RG06", "RG07", "RG08"), "cumple")
    assert DEMO.read_bytes() == original
    example = Path(__file__).resolve().parents[2] / "documentos/ejemplos/PERFIL_CORRESPONDENCIAS_ECOMMERCE.json"
    assert json.loads(example.read_text(encoding="utf-8")) == capture["correspondenceProfile"]


@pytest.mark.parametrize("error", ["schema", "project-id", "snapshot-id", "scope", "embedded-id"])
def test_invalid_capture_contract_is_rejected(capture, error):
    if error == "schema":
        capture["schemaVersion"] = True
    elif error == "project-id":
        capture["projectId"] = " "
    elif error == "snapshot-id":
        capture["snapshotId"] = None
    elif error == "scope":
        capture["scope"] = "unknown"
    else:
        capture["project"]["id"] = "another-project"
    with pytest.raises(ValueError):
        validate_correspondences(capture)

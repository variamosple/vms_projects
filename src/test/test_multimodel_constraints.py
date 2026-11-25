import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.multimodel_validation.constraints import validate_constraints
from src.multimodel_validation.transition import validate_transition


DEMO = Path(__file__).parent / "fixtures" / "multimodel_constraints.json"
RULES = ("RG09", "RG10", "RG11", "RG12")


@pytest.fixture
def capture():
    return json.loads(DEMO.read_text(encoding="utf-8"))


def statuses(report):
    return {rule["ruleId"]: rule["status"] for rule in report["rules"]}


def models(capture):
    line = capture["project"]["productLines"][0]
    return line["domainEngineering"]["models"][0], line["applicationEngineering"]["models"][0]


def transition(capture):
    after = copy.deepcopy(capture)
    profile = after.pop("constraintProfile")
    before = copy.deepcopy(after)
    before["snapshotId"] = "constraints-before"
    return {"schemaVersion": 1, "before": before, "after": after, "constraintProfile": profile}


def test_explicit_boolean_projection_passes_across_two_distinct_languages(capture):
    original = copy.deepcopy(capture)
    report = validate_constraints(capture)
    assert statuses(report) == dict.fromkeys(RULES, "cumple")
    assert report["kind"] == "constraints"
    assert report["profileId"] == "ecommerce-boolean-criteria"
    assert report["coverage"]["effectiveScope"] == "complete"
    assert report["findings"] == []
    assert capture == original


def test_native_constraint_text_is_not_parsed_or_claimed_as_validated(capture):
    base, _ = models(capture)
    base["constraints"] = "different syntax !and invalid OCL; do not execute"
    del capture["constraintProfile"]
    report = validate_constraints(capture)
    assert statuses(report) == dict.fromkeys(RULES, "no_evaluable")
    assert {f["code"] for f in report["findings"]} == {"missing_constraint_profile"}


@pytest.mark.parametrize("scope", ["complete", "partial"])
def test_explicit_empty_profile_has_no_applicable_checks(capture, scope):
    capture["scope"] = scope
    for field in ("variables", "constraints", "dependencies", "conditions"):
        capture["constraintProfile"][field] = []
    assert statuses(validate_constraints(capture)) == dict.fromkeys(RULES, "no_aplica")


@pytest.mark.parametrize("expressions", [
    [{"var": "catalog"}, {"not": {"var": "catalog"}}],
    [{"implies": [{"var": "payment"}, {"var": "catalog"}]}, {"var": "payment"}, {"not": {"var": "catalog"}}],
    [{"or": [{"var": "payment"}, {"var": "catalog"}]}, {"not": {"var": "payment"}}, {"not": {"var": "catalog"}}],
])
def test_rg09_finds_joint_contradictions_including_three_way_cases(capture, expressions):
    profile = capture["constraintProfile"]
    profile["constraints"] = [{"id": f"clause-{i}", "modelId": "feature-model" if i == 0 else "component-model", "expression": expr}
                              for i, expr in enumerate(expressions)]
    report = validate_constraints(capture)
    assert statuses(report)["RG09"] == "incumple"
    finding = next(f for f in report["findings"] if f["ruleIds"] == ["RG09"])
    assert finding["evidence"]["criterionIds"] == [f"clause-{i}" for i in range(len(expressions))]
    assert statuses(report)["RG10"] == "no_evaluable"  # Do not blame an already inconsistent context on the dependency.


def test_rg09_compatibility_is_not_the_truth_of_the_current_assignment(capture):
    profile = capture["constraintProfile"]
    profile["constraints"][0]["expression"] = {"not": {"var": "payment"}}
    # Current payment is true, but a compatible assignment exists (payment=false, catalog=true).
    assert statuses(validate_constraints(capture))["RG09"] == "cumple"


def test_rg10_detects_requirement_incompatible_with_other_artifact_constraints(capture):
    capture["constraintProfile"]["constraints"][0]["expression"] = True
    capture["constraintProfile"]["constraints"][1]["expression"] = {"not": {"var": "catalog"}}
    report = validate_constraints(capture)
    assert statuses(report)["RG09"] == "cumple"
    assert statuses(report)["RG10"] == "incumple"
    assert any(f["code"] == "incompatible_constraint_dependency" for f in report["findings"])


def test_rg10_reports_an_observed_active_dependency_with_a_false_target(capture):
    _, component = models(capture)
    component["elements"][0]["attributes"]["enabled"] = False
    result = validate_constraints(capture)
    assert statuses(result)["RG10"] == "incumple"
    assert statuses(result)["RG12"] == "incumple"
    assert any(f["code"] == "unsatisfied_constraint_dependency" for f in result["findings"])


def test_false_guard_deactivates_dependency_even_with_an_unknown_target(capture):
    feature, component = models(capture)
    feature["elements"][1]["properties"][0]["value"] = "Unselected"
    component["elements"][0]["attributes"].clear()
    assert statuses(validate_constraints(capture))["RG10"] == "no_aplica"


def test_false_and_unknown_guard_is_inactive(capture):
    profile = capture["constraintProfile"]
    profile["dependencies"][0]["when"] = {"and": [False, {"var": "catalog"}]}
    models(capture)[0]["elements"][0]["properties"] = []
    assert statuses(validate_constraints(capture))["RG10"] == "no_aplica"


def test_observed_activation_forbidden_by_constraints_is_a_violation(capture):
    capture["constraintProfile"]["constraints"][0]["expression"] = {"not": {"var": "payment"}}
    report = validate_constraints(capture)
    assert statuses(report)["RG10"] == "incumple"
    assert any(f["code"] == "forbidden_constraint_activation" for f in report["findings"])


def test_dependency_requires_scoped_observations_in_its_declared_models(capture):
    profile = capture["constraintProfile"]
    profile["variables"][1]["bindings"] = profile["variables"][1]["bindings"][:1]
    assert statuses(validate_constraints(capture))["RG10"] == "no_evaluable"


def test_rg11_detects_opposed_conditions_of_one_explicit_concept(capture):
    profile = capture["constraintProfile"]
    profile["conditions"][0]["expression"] = {"var": "catalog"}
    profile["conditions"][1]["expression"] = {"not": {"var": "catalog"}}
    result = validate_constraints(capture)
    assert statuses(result)["RG11"] == "incumple"
    assert statuses(result)["RG09"] == "cumple"


def test_rg11_does_not_infer_same_concept_from_identical_element_names(capture):
    capture["constraintProfile"]["conditions"][1]["conceptId"] = "another-concept"
    assert statuses(validate_constraints(capture))["RG11"] == "no_aplica"


def test_rg12_requires_observed_disagreement_between_distinct_models(capture):
    p = capture["constraintProfile"]
    p["constraints"] = p["conditions"] = p["dependencies"] = []
    p["variables"] = p["variables"][:1]
    extra = copy.deepcopy(p["variables"][0]["bindings"][0])
    extra["elementId"] = "payment"
    p["variables"][0]["bindings"].append(extra)
    feature, component = models(capture)
    feature["elements"][1]["properties"][0]["value"] = "Unselected"
    component["elements"][0]["attributes"] = {}
    assert statuses(validate_constraints(capture))["RG12"] == "no_evaluable"


def test_model_level_attributes_and_explicit_string_encodings_are_supported(capture):
    feature, component = models(capture)
    feature["flags"] = {"checkout": "yes"}
    component["flags"] = {"checkout": "on"}
    capture["constraintProfile"]["variables"][0]["bindings"] = [
        {"modelId": "feature-model", "attribute": {"path": ["flags", "checkout"]}, "trueValue": "yes", "falseValue": "no"},
        {"modelId": "component-model", "attribute": {"path": ["flags", "checkout"]}, "trueValue": "on", "falseValue": "off"},
    ]
    assert statuses(validate_constraints(capture)) == dict.fromkeys(RULES, "cumple")


@pytest.mark.parametrize("value", [1, "true", None, "anything"])
def test_values_are_typed_and_not_coerced_to_booleans(capture, value):
    models(capture)[1]["elements"][0]["attributes"]["enabled"] = value
    result = statuses(validate_constraints(capture))
    assert result["RG12"] == result["RG09"] == "no_evaluable"


@pytest.mark.parametrize("problem", ["missing-property", "duplicate-property", "malformed-properties", "missing-element", "duplicate-element", "missing-model", "duplicate-model"])
def test_missing_or_ambiguous_data_is_not_a_semantic_contradiction(capture, problem):
    feature, _ = models(capture)
    if problem == "missing-property":
        feature["elements"][0]["properties"] = []
    elif problem == "duplicate-property":
        feature["elements"][0]["properties"] *= 2
    elif problem == "malformed-properties":
        feature["elements"][0]["properties"].append(None)
    elif problem == "missing-element":
        feature["elements"].pop(0)
    elif problem == "duplicate-element":
        feature["elements"].append(copy.deepcopy(feature["elements"][0]))
    elif problem == "missing-model":
        capture["project"]["productLines"][0]["domainEngineering"]["models"] = []
    else:
        capture["project"]["productLines"][0]["domainEngineering"]["models"].append(copy.deepcopy(feature))
    assert statuses(validate_constraints(capture))["RG12"] == "no_evaluable"


def test_partial_capture_cannot_produce_complete_passes_but_can_show_observed_conflicts(capture):
    capture["scope"] = "partial"
    assert statuses(validate_constraints(capture)) == dict.fromkeys(RULES, "no_evaluable")
    models(capture)[1]["elements"][0]["attributes"]["enabled"] = False
    assert statuses(validate_constraints(capture))["RG12"] == "incumple"


@pytest.mark.parametrize("problem", ["version", "extra-field", "variable-duplicate", "unknown-variable", "unsupported-operator", "expression-text", "empty-and", "bad-implies", "same-model-dependency", "duplicate-criterion", "unpaired-values", "equal-values", "nan", "depth", "too-many-variables", "duplicate-binding"])
def test_invalid_profiles_are_isolated_and_never_executed(capture, problem):
    p = capture["constraintProfile"]
    if problem == "version": p["schemaVersion"] = True
    elif problem == "extra-field": p["execute"] = "do not execute"
    elif problem == "variable-duplicate": p["variables"].append(copy.deepcopy(p["variables"][0]))
    elif problem == "unknown-variable": p["constraints"][0]["expression"] = {"var": "unknown"}
    elif problem == "unsupported-operator": p["constraints"][0]["expression"] = {"eval": "__import__('os')"}
    elif problem == "expression-text": p["constraints"][0]["expression"] = "catalog && payment"
    elif problem == "empty-and": p["constraints"][0]["expression"] = {"and": []}
    elif problem == "bad-implies": p["constraints"][0]["expression"] = {"implies": [True, False, True]}
    elif problem == "same-model-dependency": p["dependencies"][0]["targetModelId"] = "feature-model"
    elif problem == "duplicate-criterion": p["constraints"][0]["id"] = p["dependencies"][0]["id"]
    elif problem == "unpaired-values": del p["variables"][0]["bindings"][0]["falseValue"]
    elif problem == "equal-values": p["variables"][0]["bindings"][0]["falseValue"] = "Selected"
    elif problem == "nan": p["variables"][0]["bindings"][0]["falseValue"] = float("nan")
    elif problem == "depth":
        expr = True
        for _ in range(14): expr = {"not": expr}
        p["constraints"][0]["expression"] = expr
    elif problem == "too-many-variables":
        p["variables"] += [{**copy.deepcopy(p["variables"][0]), "id": f"new-{i}"} for i in range(11)]
    else: p["variables"][0]["bindings"].append(copy.deepcopy(p["variables"][0]["bindings"][0]))
    report = validate_constraints(capture)
    assert statuses(report) == dict.fromkeys(RULES, "no_evaluable")
    assert {f["code"] for f in report["findings"]} == {"invalid_constraint_profile"}


def test_work_budget_discards_partial_passes(capture):
    report = validate_constraints(capture, max_checks=9)
    assert statuses(report) == dict.fromkeys(RULES, "no_evaluable")
    assert {f["code"] for f in report["findings"]} == {"constraint_evaluation_limit"}


def test_exhaustive_search_has_a_real_work_bound(capture):
    p = capture["constraintProfile"]
    p["variables"] = [{**copy.deepcopy(p["variables"][0]), "id": f"v-{i}"} for i in range(12)]
    p["constraints"] = [
        {"id": "large", "modelId": "feature-model", "expression": {"and": [{"var": v["id"]} for v in p["variables"]]}},
        {"id": "other", "modelId": "component-model", "expression": True},
    ]
    p["conditions"] = p["dependencies"] = []
    report = validate_constraints(capture)
    assert statuses(report) == dict.fromkeys(RULES, "no_evaluable")
    assert {f["code"] for f in report["findings"]} == {"constraint_evaluation_limit"}


def test_new_report_preserves_all_prior_increment_results(capture):
    request = transition(capture)
    baseline = validate_transition({k: v for k, v in request.items() if k != "constraintProfile"})
    result = validate_transition(request)
    assert statuses(result["constraintValidation"]) == dict.fromkeys(RULES, "cumple")
    assert {k: v for k, v in result.items() if k != "constraintValidation"} == {k: v for k, v in baseline.items() if k != "constraintValidation"}
    request["constraintProfile"]["unknown"] = True
    invalid = validate_transition(request)
    assert statuses(invalid["constraintValidation"]) == dict.fromkeys(RULES, "no_evaluable")
    assert invalid["structuralValidation"] == baseline["structuralValidation"]
    assert invalid["correspondenceValidation"] == baseline["correspondenceValidation"]
    limited = validate_transition(transition(capture), max_constraint_checks=0)
    assert statuses(limited["constraintValidation"]) == dict.fromkeys(RULES, "no_evaluable")
    assert limited["rules"] == baseline["rules"]


def test_cli_runs_without_site_packages_or_services():
    result = subprocess.run([sys.executable, "-B", "-S", "-m", "src.multimodel_validation.constraints", str(DEMO)],
                            capture_output=True, text=True, check=True)
    assert statuses(json.loads(result.stdout)) == dict.fromkeys(RULES, "cumple")

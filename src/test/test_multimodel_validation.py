import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.multimodel_validation import validate_snapshot


DEMO = Path(__file__).parent / "fixtures" / "multimodel_snapshot.json"


@pytest.fixture
def capture():
    request = json.loads(DEMO.read_text(encoding="utf-8"))
    line = request["project"]["productLines"][0]
    application = line["applicationEngineering"]["applications"][0]
    domain = line["domainEngineering"]["models"][0]
    adapted = application["adaptations"][0]["models"][0]
    adapted["elements"][1]["sourceModelElements"][0]["elementId"] = "item"
    return request, line, application, domain, adapted


def statuses(result):
    return {rule["ruleId"]: rule["status"] for rule in result["rules"]}


def findings(result, code):
    return [finding for finding in result["findings"] if finding["code"] == code]


def test_two_languages_and_cross_model_ids(capture):
    request, _, _, _, _ = capture
    result = validate_snapshot(request)
    assert statuses(result) == dict.fromkeys(("RG01", "RG02", "RG03", "RG04", "RG05"), "cumple")
    assert result["findings"] == []
    assert result["coverage"]["effectiveScope"] == "complete"
    assert len(result["rules"][2]["modelPaths"]) == 2


def test_walks_all_five_model_locations(capture):
    request, line, application, _, _ = capture
    for container, name in (
        (line["scope"], "scope"), (line["applicationEngineering"], "engineering"),
        (application, "application"),
    ):
        container["models"].append({"id": name, "elements": [], "relationships": []})
    models = validate_snapshot(request)["coverage"]["models"]
    assert [model["modelId"] for model in models] == [
        "scope", "domain", "engineering", "application", "adapted"
    ]
    assert models[3]["path"].endswith("/applicationEngineering/applications/0/models/0")
    assert models[4]["path"].endswith("/applications/0/adaptations/0/models/0")


def test_duplicate_element_id_is_detected_before_resolving(capture):
    request, _, _, domain, _ = capture
    domain["elements"].append({"id": "shared", "type": "Item"})
    result = validate_snapshot(request)
    duplicate, = findings(result, "duplicate_element_id")
    assert duplicate["ruleIds"] == ["RG03"]
    assert duplicate["location"]["elementId"] == "shared"
    assert len(duplicate["evidence"]["paths"]) == 2
    assert statuses(result)["RG03"] == "incumple"
    assert any(finding["location"]["referenceId"] == "r-1"
               for finding in findings(result, "ambiguous_element"))


def test_duplicate_model_id_cannot_resolve_origin(capture):
    request, line, _, domain, _ = capture
    line["scope"]["models"].append(copy.deepcopy(domain))
    result = validate_snapshot(request)
    assert statuses(result)["RG03"] == "cumple"
    assert statuses(result)["RG05"] == "incumple"
    assert findings(result, "ambiguous_model")


@pytest.mark.parametrize("field", ["sourceId", "targetId"])
def test_missing_internal_endpoint_consolidates_rg01_and_rg02(capture, field):
    request, _, _, domain, _ = capture
    domain["relationships"][0][field] = "variant-2"
    result = validate_snapshot(request)
    missing, = findings(result, "missing_element")
    assert missing["ruleIds"] == ["RG01", "RG02"]
    assert missing["location"]["path"].endswith(f"/relationships/0/{field}")
    assert missing["evidence"]["targetModelId"] == "domain"
    assert missing["status"] == "incumple"


def test_broken_element_origin_is_one_finding_for_three_rules():
    result = validate_snapshot(json.loads(DEMO.read_text(encoding="utf-8")))
    finding, = result["findings"]
    assert finding["ruleIds"] == ["RG01", "RG02", "RG05"]
    assert finding["status"] == "incumple"
    assert finding["location"]["modelId"] == "adapted"
    assert finding["location"]["elementId"] == "variant-2"
    assert finding["evidence"] == {"targetModelId": "domain", "targetElementId": "removed"}


def test_unidentified_source_cannot_pass_correspondence(capture):
    request, _, _, _, adapted = capture
    del adapted["elements"][1]["id"]
    result = validate_snapshot(request)
    assert statuses(result)["RG05"] == "no_evaluable"
    assert findings(result, "unidentified_origin")


def test_model_origin_must_point_to_a_different_model(capture):
    request, _, _, _, adapted = capture
    adapted["sourceModelIds"] = ["adapted"]
    result = validate_snapshot(request)
    same_model, = findings(result, "same_model_origin")
    assert same_model["ruleIds"] == ["RG02", "RG05"]
    assert statuses(result)["RG01"] == "cumple"


def test_missing_model_origin_fails_in_a_complete_capture(capture):
    request, _, _, _, adapted = capture
    adapted["sourceModelIds"] = ["missing-model"]
    result = validate_snapshot(request)
    missing, = findings(result, "missing_model")
    assert missing["ruleIds"] == ["RG02", "RG05"]
    assert missing["evidence"]["targetModelId"] == "missing-model"


def test_names_do_not_create_correspondences(capture):
    request, _, _, domain, adapted = capture
    del adapted["sourceModelIds"]
    for element in adapted["elements"]:
        del element["sourceModelElements"]
        element["name"] = "Common name"
    domain["elements"][0]["name"] = "Common name"
    assert statuses(validate_snapshot(request))["RG05"] == "no_aplica"


@pytest.mark.parametrize("declared_scope", ["partial", "complete"])
def test_missing_model_under_an_omitted_section_is_not_a_violation(capture, declared_scope):
    request, line, _, _, _ = capture
    request["scope"] = declared_scope
    del line["domainEngineering"]
    result = validate_snapshot(request)
    assert result["coverage"]["effectiveScope"] == "partial"
    assert "/project/productLines/0/domainEngineering" in result["coverage"]["incompletePaths"]
    assert all(statuses(result)[rule] == "no_evaluable" for rule in ("RG01", "RG02", "RG05"))
    assert findings(result, "target_outside_coverage")
    assert all(finding["status"] != "incumple" for finding in result["findings"])


def test_partial_snapshot_with_visible_targets_can_resolve_current_links(capture):
    request, _, _, _, _ = capture
    request["scope"] = "partial"
    result = validate_snapshot(request)
    assert statuses(result)["RG01"] == "no_evaluable"
    assert statuses(result)["RG02"] == statuses(result)["RG05"] == "cumple"


def test_partial_snapshot_does_not_hide_an_explicit_invalid_id(capture):
    request, _, _, _, adapted = capture
    request["scope"] = "partial"
    adapted["elements"][0]["sourceModelElements"] = [{"modelId": "outside", "elementId": ""}]
    result = validate_snapshot(request)
    invalid, = findings(result, "invalid_identifier")
    assert invalid["ruleIds"] == ["RG02", "RG05"]
    assert invalid["location"]["path"].endswith("/sourceModelElements/0/elementId")


def test_missing_or_unsupported_metamodel_is_not_an_error(capture):
    request, _, _, _, _ = capture
    del request["metamodels"]["8"]
    request["metamodels"]["7"] = {"abstractSyntax": "{incomplete"}
    result = validate_snapshot(request)
    assert statuses(result)["RG04"] == "no_evaluable"
    assert len(findings(result, "missing_relationship_context")) == 2


def test_explicit_metamodel_rejects_unknown_type_and_incompatible_endpoints(capture):
    request, _, _, domain, adapted = capture
    domain["relationships"][0]["sourceId"] = "item"
    adapted["relationships"][0]["type"] = "Unknown"
    result = validate_snapshot(request)
    assert statuses(result)["RG02"] == "cumple"
    assert statuses(result)["RG04"] == "incumple"
    incompatible, = findings(result, "incompatible_endpoint_types")
    assert incompatible["evidence"]["sourceType"] == "Item"
    assert incompatible["evidence"]["allowedSourceType"] == "Container"
    assert findings(result, "unknown_relationship_type")


def test_json_abstract_syntax_and_incomplete_endpoint_definition(capture):
    request, _, _, _, _ = capture
    syntax = request["metamodels"]["7"]["abstractSyntax"]
    request["metamodels"]["7"]["abstractSyntax"] = json.dumps(syntax)
    request["metamodels"]["8"]["relationships"]["Pairs"]["target"] = "Variant"
    result = validate_snapshot(request)
    assert statuses(result)["RG04"] == "no_evaluable"
    assert result["rules"][3]["checkCounts"] == {"cumple": 1, "no_evaluable": 1}
    assert findings(result, "incomplete_relationship_definition")


def test_empty_collections_are_not_applicable(capture):
    request, _, _, _, _ = capture
    request["project"]["productLines"] = []
    result = validate_snapshot(request)
    assert result["coverage"]["effectiveScope"] == "complete"
    assert set(statuses(result).values()) == {"no_aplica"}


@pytest.mark.parametrize("value", ["", None, False, 13, " item ", "item\n", {}, []])
def test_malformed_explicit_reference_id_is_a_violation(capture, value):
    request, _, _, domain, _ = capture
    domain["relationships"][0]["targetId"] = value
    result = validate_snapshot(request)
    assert statuses(result)["RG02"] == "incumple"
    assert statuses(result)["RG01"] == "no_evaluable"
    assert findings(result, "invalid_identifier")


def test_incomplete_objects_and_unsupported_origin_are_not_violations(capture):
    request, _, _, domain, adapted = capture
    domain["elements"].extend([{}, None, []])
    del domain["relationships"][0]["sourceId"]
    del adapted["relationships"]
    adapted["elements"][1]["sourceModelElements"] = [
        {"modelId": "domain", "elementId": "item", "revisionId": "old"}
    ]
    result = validate_snapshot(request)
    assert result["coverage"]["effectiveScope"] == "partial"
    assert {"no_evaluable"} == set(statuses(result).values())
    assert findings(result, "incomplete_model_data")
    assert findings(result, "missing_identifier")
    assert findings(result, "unsupported_origin_format")


def test_existing_legacy_fixture_reports_omitted_scope_without_violations(capture):
    request, _, _, _, _ = capture
    request["project"] = json.loads((DEMO.parent.parent / "test.json").read_text(encoding="utf-8"))
    del request["metamodels"]
    result = validate_snapshot(request)
    assert result["coverage"]["effectiveScope"] == "partial"
    assert "/project/productLines/0/scope" in result["coverage"]["incompletePaths"]
    assert all(finding["status"] == "no_evaluable" for finding in result["findings"])


def test_selection_is_deterministic_and_input_is_immutable(capture):
    request, _, _, domain, _ = capture
    request["ruleIds"] = ["RG03", "RG02", "RG03"]
    domain["elements"].append({"id": "shared"})
    original = copy.deepcopy(request)
    result = validate_snapshot(request)
    assert list(statuses(result)) == ["RG02", "RG03"]
    assert statuses(result) == {"RG02": "incumple", "RG03": "incumple"}
    assert result == validate_snapshot(request)
    assert request == original


@pytest.mark.parametrize("key,value", [("schemaVersion", True), ("schemaVersion", 2), ("ruleIds", ["RG06"])])
def test_contract_rejects_unsupported_versions_and_rules(capture, key, value):
    request, _, _, _, _ = capture
    request[key] = value
    with pytest.raises(ValueError):
        validate_snapshot(request)


def test_cli_with_standard_library_only_and_read_only_fixture():
    original = DEMO.read_bytes()
    completed = subprocess.run(
        [sys.executable, "-B", "-S", "-m", "src.multimodel_validation", str(DEMO)],
        cwd=DEMO.parents[3],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == validate_snapshot(json.loads(original))
    assert DEMO.read_bytes() == original

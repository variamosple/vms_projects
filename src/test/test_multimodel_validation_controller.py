import json
import copy
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.infrastructure.multimodel_validation_controller import router
from src.infrastructure import multimodel_validation_controller as controller


_PATH = "/api/ai/validate-snapshot"
_TRANSITION_PATH = "/api/ai/validate-transition"
_AUTH = {"Authorization": "Bearer test"}


@pytest.fixture
def client():
    def require_auth(request: Request):
        if request.headers.get("Authorization") != _AUTH["Authorization"]:
            raise HTTPException(401)

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(require_auth)])
    with TestClient(app) as isolated_client:
        yield isolated_client


def test_reports_informational_findings_for_a_supplied_snapshot(client):
    fixture = Path(__file__).parent / "fixtures" / "multimodel_snapshot.json"
    capture = json.loads(fixture.read_text(encoding="utf-8"))
    response = client.post(_PATH, json=capture, headers=_AUTH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert report["snapshotId"] == capture["snapshotId"]
    assert len(report["coverage"]["models"]) == 2
    finding, = report["findings"]
    assert finding["ruleIds"] == ["RG01", "RG02", "RG05"]
    assert finding["status"] == "incumple"


@pytest.mark.parametrize("path", [_PATH, _TRANSITION_PATH])
def test_rejects_requests_without_authentication(client, path):
    response = client.post(path, json={})
    assert response.status_code == 401


@pytest.mark.parametrize("content", ["{", '{"schemaVersion":2}', '"private payload"'])
def test_invalid_input_does_not_return_the_payload(client, content):
    response = client.post(_PATH, content=content, headers=_AUTH)
    assert response.status_code == 422
    assert response.json() == {"detail": "La captura JSON no cumple el contrato de validación."}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "content",
    [" " * (1024 * 1024 + 1), json.dumps([None] * 20_000)],
    ids=["byte-limit", "value-limit"],
)
def test_rejects_captures_over_size_or_complexity_limits(client, content):
    response = client.post(_PATH, content=content, headers=_AUTH)
    assert response.status_code == 413


def transition_capture():
    fixture = Path(__file__).parent / "fixtures" / "multimodel_transition.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_transition_reports_deletion_violations_without_rejecting_the_change(client):
    response = client.post(_TRANSITION_PATH, json=transition_capture(), headers=_AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert report["kind"] == "transition"
    assert {rule["ruleId"]: rule["status"] for rule in report["rules"]} == {
        "RG13": "cumple", "RG14": "incumple", "RG15": "incumple", "RG16": "incumple",
    }
    assert len([finding for finding in report["findings"] if "RG15" in finding["ruleIds"]]) == 2


def test_transition_reports_rename_impact_without_a_deletion(client):
    capture = transition_capture()
    capture["after"]["project"] = copy.deepcopy(capture["before"]["project"])
    capture["after"]["project"]["productLines"][0]["domainEngineering"]["models"][0]["elements"][1]["name"] = "CatalogoPrueba"
    response = client.post(_TRANSITION_PATH, json=capture, headers=_AUTH)
    assert response.status_code == 200
    report = response.json()
    assert report["beforeSnapshotId"] == capture["before"]["snapshotId"]
    assert report["afterSnapshotId"] == capture["after"]["snapshotId"]
    assert {rule["ruleId"]: rule["status"] for rule in report["rules"]} == {
        "RG13": "cumple", "RG14": "no_evaluable", "RG15": "no_aplica", "RG16": "cumple",
    }
    assert any(impact["dependent"]["modelId"] == "derived" for impact in report["impacts"])


@pytest.mark.parametrize("content", ["{", '{"schemaVersion":1}', '"private payload"'])
def test_transition_invalid_input_is_generic_and_not_cached(client, content):
    response = client.post(_TRANSITION_PATH, content=content, headers=_AUTH)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "La captura JSON no cumple el contrato de validación."}


@pytest.mark.parametrize("limit", ["bytes", "values"])
def test_transition_limits_account_for_two_captures(client, limit):
    content = " " * (2 * 1024 * 1024 + 1) if limit == "bytes" else json.dumps([None] * 40_000)
    response = client.post(_TRANSITION_PATH, content=content, headers=_AUTH)
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"


def test_transition_bounds_expansion_of_potential_impacts(client, monkeypatch):
    monkeypatch.setattr(controller, "_MAX_TRANSITION_IMPACTS", 1)
    response = client.post(_TRANSITION_PATH, json=transition_capture(), headers=_AUTH)
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"


def test_validation_routes_are_unique_and_keep_their_http_methods(client):
    for path in (_PATH, _TRANSITION_PATH):
        matching = [route for route in client.app.routes if getattr(route, "path", None) == path]
        assert len(matching) == 1
        assert matching[0].methods == {"POST"}
        assert client.get(path, headers=_AUTH).status_code == 405


@pytest.mark.parametrize("broken", [False, True])
def test_rg16_matches_the_existing_snapshot_endpoint_on_the_same_after_state(client, broken):
    capture = transition_capture()
    if not broken:
        capture["after"]["project"] = copy.deepcopy(capture["before"]["project"])
        capture["after"]["project"]["productLines"][0]["domainEngineering"]["models"][0]["elements"][1]["name"] = "Renamed"
    transition = client.post(_TRANSITION_PATH, json=capture, headers=_AUTH)
    snapshot = client.post(_PATH, json=capture["after"], headers=_AUTH)
    assert transition.status_code == snapshot.status_code == 200
    assert transition.json()["structuralValidation"]["after"] == snapshot.json()
    assert next(rule["status"] for rule in transition.json()["rules"] if rule["ruleId"] == "RG16") == ("incumple" if broken else "cumple")


def test_invalid_metamodel_in_transition_returns_contract_error_without_echoing_input(client):
    capture = transition_capture()
    capture["after"]["metamodels"] = ["private"]
    response = client.post(_TRANSITION_PATH, json=capture, headers=_AUTH)
    assert response.status_code == 422
    assert response.json() == {"detail": "La captura JSON no cumple el contrato de validación."}


def correspondence_transition():
    path = Path(__file__).parent / "fixtures" / "multimodel_correspondence.json"
    capture = json.loads(path.read_text(encoding="utf-8"))
    profile = capture.pop("correspondenceProfile")
    before = copy.deepcopy(capture)
    before["snapshotId"] = "before-correspondences"
    return {"schemaVersion": 1, "before": before, "after": capture, "correspondenceProfile": profile}


def test_correspondence_report_uses_the_existing_authenticated_route(client):
    response = client.post(_TRANSITION_PATH, json=correspondence_transition(), headers=_AUTH)
    assert response.status_code == 200
    report = response.json()["correspondenceValidation"]
    assert report["kind"] == "correspondences"
    assert {rule["ruleId"]: rule["status"] for rule in report["rules"]} == {"RG06": "cumple", "RG07": "cumple", "RG08": "cumple"}


def test_invalid_correspondence_profile_preserves_the_prior_increments(client):
    request = correspondence_transition()
    baseline = client.post(_TRANSITION_PATH, json=request, headers=_AUTH).json()
    request["correspondenceProfile"]["schemaVersion"] = 99
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    report = response.json()
    assert report["rules"] == baseline["rules"]
    assert report["structuralValidation"] == baseline["structuralValidation"]
    assert all(rule["status"] == "no_evaluable" for rule in report["correspondenceValidation"]["rules"])


def test_correspondence_work_limit_preserves_the_previous_reports(client):
    request = correspondence_transition()
    original = request["correspondenceProfile"]["correspondences"][0]
    request["correspondenceProfile"]["correspondences"] = [{**copy.deepcopy(original), "id": f"criterion-{index}"} for index in range(1800)]
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    report = response.json()
    assert {rule["ruleId"] for rule in report["rules"]} == {"RG13", "RG14", "RG15", "RG16"}
    assert {finding["code"] for finding in report["correspondenceValidation"]["findings"]} == {"correspondence_evaluation_limit"}


def constraint_transition():
    path = Path(__file__).parent / "fixtures" / "multimodel_constraints.json"
    after = json.loads(path.read_text(encoding="utf-8"))
    profile = after.pop("constraintProfile")
    before = copy.deepcopy(after)
    before["snapshotId"] = "before-constraints"
    return {"schemaVersion": 1, "before": before, "after": after, "constraintProfile": profile}


def test_fourth_increment_uses_the_same_route_with_no_store_and_authentication(client):
    request = constraint_transition()
    assert client.post(_TRANSITION_PATH, json=request).status_code == 401
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()["constraintValidation"]
    assert report["kind"] == "constraints"
    assert report["snapshotId"] == request["after"]["snapshotId"]
    assert all(rule["status"] == "cumple" for rule in report["rules"])


@pytest.mark.parametrize("mode", ["absent", "invalid", "contradiction", "limit"])
def test_constraint_issues_do_not_suppress_previous_reports_or_change_http_success(client, mode):
    request = constraint_transition()
    baseline = client.post(_TRANSITION_PATH, json=request, headers=_AUTH).json()
    if mode == "absent":
        del request["constraintProfile"]
    elif mode == "invalid":
        request["constraintProfile"]["schemaVersion"] = 2
    elif mode == "contradiction":
        request["constraintProfile"]["conditions"][1]["expression"] = {"not": {"var": "catalog"}}
    else:
        v = request["constraintProfile"]["variables"][0]
        request["constraintProfile"]["variables"] = [{**copy.deepcopy(v), "id": f"v-{i}"} for i in range(12)]
        request["constraintProfile"]["constraints"] = [
            {"id": "large", "modelId": "feature-model", "expression": {"and": [{"var": f"v-{i}"} for i in range(12)]}},
            {"id": "other", "modelId": "component-model", "expression": True},
        ]
        request["constraintProfile"]["conditions"] = request["constraintProfile"]["dependencies"] = []
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    result = response.json()
    assert {k: v for k, v in result.items() if k != "constraintValidation"} == {k: v for k, v in baseline.items() if k != "constraintValidation"}
    statuses = {r["ruleId"]: r["status"] for r in result["constraintValidation"]["rules"]}
    if mode == "contradiction":
        assert statuses["RG11"] == "incumple"
    else:
        assert set(statuses.values()) == {"no_evaluable"}


def version_transition():
    path = Path(__file__).parent / "fixtures" / "multimodel_versions.json"
    after = json.loads(path.read_text(encoding="utf-8"))
    context = after.pop("versionContext")
    before = copy.deepcopy(after)
    before["snapshotId"] = "versions-before"
    return {"schemaVersion": 1, "before": before, "after": after, "versionContext": context}


def test_fifth_increment_uses_the_existing_authenticated_route(client):
    request = version_transition()
    assert client.post(_TRANSITION_PATH, json=request).status_code == 401
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert all(r["status"] == "cumple" for r in response.json()["versionValidation"]["rules"])


@pytest.mark.parametrize("mode", ["invalid", "missing", "incompatible"])
def test_version_findings_preserve_the_first_four_reports_and_http_success(client, mode):
    request = version_transition()
    baseline = client.post(_TRANSITION_PATH, json=request, headers=_AUTH).json()
    if mode == "invalid": request["versionContext"]["schemaVersion"] = 9
    elif mode == "missing": del request["versionContext"]
    else: request["versionContext"]["compatibility"][0]["compatible"] = False
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    report = response.json()
    assert {k: v for k, v in report.items() if k != "versionValidation"} == {k: v for k, v in baseline.items() if k != "versionValidation"}
    expected = "incumple" if mode == "incompatible" else "no_evaluable"
    assert report["versionValidation"]["rules"][0]["status"] == expected


def trace_transition():
    return json.loads((Path(__file__).parent / "fixtures" / "multimodel_traceability.json").read_text(encoding="utf-8"))


def test_sixth_increment_uses_the_existing_authenticated_route_and_no_store(client):
    request = trace_transition()
    assert client.post(_TRANSITION_PATH, json=request).status_code == 401
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert all(r["status"] == "cumple" for r in response.json()["traceabilityValidation"]["rules"])


@pytest.mark.parametrize("mode", ["invalid-context", "bad-history", "missing-context", "bad-attempt"])
def test_trace_issues_do_not_suppress_the_five_previous_reports(client, mode):
    request = trace_transition()
    baseline = client.post(_TRANSITION_PATH, json=request, headers=_AUTH).json()
    if mode == "invalid-context": request["traceContext"]["schemaVersion"] = 0
    elif mode == "bad-history": request["traceContext"]["history"][0]["oldValue"]["name"] = "Other"
    elif mode == "missing-context": del request["traceContext"]
    else: request["attemptContext"]["beforeSnapshotId"] = "other"
    response = client.post(_TRANSITION_PATH, json=request, headers=_AUTH)
    assert response.status_code == 200
    report = response.json()
    assert {k: v for k, v in report.items() if k != "traceabilityValidation"} == {k: v for k, v in baseline.items() if k != "traceabilityValidation"}
    rules = {r["ruleId"]: r["status"] for r in report["traceabilityValidation"]["rules"]}
    assert rules["RG21" if mode != "bad-attempt" else "RG20"] == ("incumple" if mode == "bad-history" else "no_evaluable")

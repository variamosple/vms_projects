import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.infrastructure.multimodel_validation_controller import router


_PATH = "/api/ai/validate-snapshot"
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


def test_rejects_requests_without_authentication(client):
    response = client.post(_PATH, json={})
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

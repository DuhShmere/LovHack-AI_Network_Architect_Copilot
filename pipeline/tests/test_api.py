"""Samir: /design route error handling. The LLM layer is mocked; no real API calls."""

import pytest
from fastapi.testclient import TestClient

from pipeline.api import routes
from pipeline.api.main import app
from pipeline.llm_layer import LLMResponseError, MissingAPIKeyError
from shared.schema import NetworkSpec

client = TestClient(app)


def _raise(exc):
    def fake_parse(_description):
        raise exc

    return fake_parse


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_parse_endpoint_returns_structured_requirements(monkeypatch):
    spec = NetworkSpec(org_name="Test Office", user_count=30)
    monkeypatch.setattr(routes, "parse_requirements", lambda _description: spec)

    resp = client.post("/parse", json={"description": "30-person office"})

    assert resp.status_code == 200
    assert resp.json()["org_name"] == "Test Office"
    assert resp.json()["user_count"] == 30


def test_design_returns_plan_validation_and_configs(monkeypatch):
    spec = NetworkSpec(
        org_name="Test Office",
        user_count=30,
        needs_guest_wifi=True,
        department_segments=["staff"],
    )
    monkeypatch.setattr(routes, "parse_requirements", lambda _description: spec)

    resp = client.post("/design", json={"description": "30-person office with guest Wi-Fi"})

    assert resp.status_code == 200
    result = resp.json()
    assert result["plan"]["spec"]["org_name"] == "Test Office"
    assert result["validation"]["overall_pass"] is True
    assert result["validation"]["checks"]
    assert len(result["configs"]) == len(result["plan"]["nodes"])


def test_design_returns_no_configs_when_validation_fails(monkeypatch):
    spec = NetworkSpec(
        org_name="Isolated Guest Office",
        user_count=30,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
    )
    monkeypatch.setattr(routes, "parse_requirements", lambda _description: spec)

    resp = client.post("/design", json={"description": "isolated guest Wi-Fi"})

    assert resp.status_code == 200
    result = resp.json()
    assert result["validation"]["overall_pass"] is False
    assert result["configs"] == []


def test_design_maps_not_implemented_to_501(monkeypatch):
    spec = NetworkSpec(org_name="Test Office", user_count=20)
    monkeypatch.setattr(routes, "parse_requirements", lambda _description: spec)
    monkeypatch.setattr(routes, "generate_plan", _raise(NotImplementedError("engine unavailable")))

    resp = client.post("/design", json={"description": "small office"})

    assert resp.status_code == 501
    assert resp.json()["detail"] == "engine unavailable"


@pytest.mark.parametrize("path", ["/parse", "/design"])
@pytest.mark.parametrize(
    "exc, status",
    [
        (MissingAPIKeyError("ANTHROPIC_API_KEY is not set."), 503),
        (LLMResponseError("Model response was not valid JSON"), 502),
    ],
)
def test_routes_map_llm_errors_to_http_status(monkeypatch, path, exc, status):
    monkeypatch.setattr(routes, "parse_requirements", _raise(exc))
    resp = client.post(path, json={"description": "small office"})
    assert resp.status_code == status
    assert resp.json()["detail"] == str(exc)

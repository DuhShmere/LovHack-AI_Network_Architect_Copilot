"""Offline route tests for /health, /parse, and /design."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from pipeline import llm_layer
from pipeline.api import routes
from pipeline.api.main import app
from shared.schema import NetworkPlan, NetworkSpec, ValidationCheck, ValidationReport

client = TestClient(app)
FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
VALID_SPEC_JSON = json.dumps({
    "org_name": "Acme Dental",
    "user_count": 50,
    "needs_guest_wifi": True,
    "guest_wifi_isolated": False,
    "department_segments": ["staff", "guest"],
    "redundancy": "dual_wan",
    "preferred_base_cidr": None,
    "raw_notes": None,
})


@pytest.fixture
def mock_llm_client(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(llm_layer, "get_client", lambda: fake_client)
    return fake_client


def fake_response(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def rate_limit_error():
    return anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=FAKE_REQUEST), body=None
    )


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("description", ["", "   ", "\n\t"])
def test_design_rejects_blank_description(description):
    response = client.post("/design", json={"description": description})

    assert response.status_code == 422


@pytest.mark.parametrize("description", ["", "   ", "\n\t"])
def test_parse_rejects_blank_description(description):
    response = client.post("/parse", json={"description": description})

    assert response.status_code == 422


def test_parse_returns_network_spec_on_success(mock_llm_client):
    mock_llm_client.messages.create.return_value = fake_response(VALID_SPEC_JSON)

    response = client.post("/parse", json={"description": "50-person office"})

    assert response.status_code == 200
    assert response.json()["org_name"] == "Acme Dental"


def test_parse_returns_422_on_parse_error(mock_llm_client):
    mock_llm_client.messages.create.return_value = fake_response("not json at all")

    response = client.post("/parse", json={"description": "50-person office"})

    assert response.status_code == 422
    assert mock_llm_client.messages.create.call_count == 2


def test_parse_returns_503_on_service_error(mock_llm_client):
    mock_llm_client.messages.create.side_effect = [rate_limit_error(), rate_limit_error()]

    response = client.post("/parse", json={"description": "50-person office"})

    assert response.status_code == 503
    assert mock_llm_client.messages.create.call_count == 2


def test_design_returns_422_when_parsing_fails(mock_llm_client):
    mock_llm_client.messages.create.return_value = fake_response("not json at all")

    response = client.post("/design", json={"description": "50-person office"})

    assert response.status_code == 422


def test_design_returns_503_when_llm_service_fails(mock_llm_client):
    mock_llm_client.messages.create.side_effect = [rate_limit_error(), rate_limit_error()]

    response = client.post("/design", json={"description": "50-person office"})

    assert response.status_code == 503


def test_design_returns_501_when_engine_is_not_implemented(mock_llm_client, monkeypatch):
    mock_llm_client.messages.create.return_value = fake_response(VALID_SPEC_JSON)

    def unavailable(_spec):
        raise NotImplementedError("engine unavailable")

    monkeypatch.setattr(routes, "generate_plan", unavailable)
    response = client.post("/design", json={"description": "50-person office"})

    assert response.status_code == 501
    assert response.json()["detail"] == "engine unavailable"


def test_design_returns_full_result_on_success(mock_llm_client, monkeypatch):
    mock_llm_client.messages.create.return_value = fake_response(VALID_SPEC_JSON)
    plan = NetworkPlan(
        spec=NetworkSpec(org_name="Acme Dental", user_count=50),
        vlans=[],
        nodes=[],
        links=[],
    )
    monkeypatch.setattr(routes, "generate_plan", lambda _spec: plan)
    monkeypatch.setattr(routes, "validate_plan", lambda _plan: ValidationReport(
        overall_pass=True,
        checks=[ValidationCheck(check_name="no_subnet_overlap", passed=True, detail="ok")],
    ))
    generate_configs = MagicMock(return_value=[])
    monkeypatch.setattr(routes, "generate_configs", generate_configs)

    response = client.post("/design", json={"description": "50-person office"})

    assert response.status_code == 200
    assert response.json()["validation"]["overall_pass"] is True
    generate_configs.assert_called_once_with(plan)


def test_design_skips_config_generation_when_validation_fails(mock_llm_client, monkeypatch):
    mock_llm_client.messages.create.return_value = fake_response(VALID_SPEC_JSON)
    plan = NetworkPlan(
        spec=NetworkSpec(org_name="Acme Dental", user_count=50),
        vlans=[],
        nodes=[],
        links=[],
    )
    monkeypatch.setattr(routes, "generate_plan", lambda _spec: plan)
    monkeypatch.setattr(routes, "validate_plan", lambda _plan: ValidationReport(
        overall_pass=False,
        checks=[ValidationCheck(check_name="no_subnet_overlap", passed=False, detail="overlap")],
    ))
    generate_configs = MagicMock(return_value=[])
    monkeypatch.setattr(routes, "generate_configs", generate_configs)

    response = client.post("/design", json={"description": "50-person office"})

    assert response.status_code == 200
    assert response.json()["validation"]["overall_pass"] is False
    assert response.json()["configs"] == []
    generate_configs.assert_not_called()

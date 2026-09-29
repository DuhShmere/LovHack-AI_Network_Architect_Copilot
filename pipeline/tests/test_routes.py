"""Samir: route-level tests for /health, /parse, /design.

These mock pipeline.llm_layer.client (and, for /design's success path,
the engine/ functions imported into routes.py) so they run without a
live Anthropic API call and without depending on engine/ being real.
"""

from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from pipeline.api.main import app
from shared.schema import NetworkPlan, NetworkSpec, ValidationCheck, ValidationReport

client = TestClient(app)

_FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

VALID_SPEC_JSON = """{
  "org_name": "Acme Dental",
  "user_count": 50,
  "needs_guest_wifi": true,
  "guest_wifi_isolated": true,
  "department_segments": ["staff", "guest"],
  "redundancy": "dual_wan",
  "preferred_base_cidr": null,
  "raw_notes": null
}"""


def _fake_llm_response(text: str) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(type="text", text=text)]
    return response


def _rate_limit_error() -> anthropic.RateLimitError:
    return anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_FAKE_REQUEST), body=None
    )


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("blank_description", ["", "   ", "\n\t"])
def test_design_rejects_blank_description(blank_description):
    r = client.post("/design", json={"description": blank_description})
    assert r.status_code == 422


@pytest.mark.parametrize("blank_description", ["", "   ", "\n\t"])
def test_parse_rejects_blank_description(blank_description):
    r = client.post("/parse", json={"description": blank_description})
    assert r.status_code == 422


@patch("pipeline.llm_layer.client")
def test_parse_returns_network_spec_on_success(mock_client):
    mock_client.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
    r = client.post("/parse", json={"description": "50-person office"})
    assert r.status_code == 200
    assert r.json()["org_name"] == "Acme Dental"


@patch("pipeline.llm_layer.client")
def test_parse_returns_422_on_parse_error(mock_client):
    mock_client.messages.create.return_value = _fake_llm_response("not json at all")
    r = client.post("/parse", json={"description": "50-person office"})
    assert r.status_code == 422


@patch("pipeline.llm_layer.client")
def test_parse_returns_503_on_service_error(mock_client):
    mock_client.messages.create.side_effect = _rate_limit_error()
    r = client.post("/parse", json={"description": "50-person office"})
    assert r.status_code == 503


@patch("pipeline.llm_layer.client")
def test_design_returns_422_when_parsing_fails(mock_client):
    mock_client.messages.create.return_value = _fake_llm_response("not json at all")
    r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 422


@patch("pipeline.llm_layer.client")
def test_design_returns_503_when_llm_service_fails(mock_client):
    mock_client.messages.create.side_effect = _rate_limit_error()
    r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 503


@patch("pipeline.llm_layer.client")
def test_design_returns_real_result_end_to_end(mock_client):
    """Only the LLM is mocked here -- generate_plan/validate_plan/generate_configs
    are the real engine/ implementations."""
    mock_client.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
    r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 200
    body = r.json()
    assert body["validation"]["overall_pass"] is True
    assert len(body["configs"]) > 0


@patch("pipeline.llm_layer.client")
def test_design_returns_422_on_plan_generation_error(mock_client):
    """engine.generator.PlanGenerationError (e.g. user_count < 1) should
    surface as 422, not an unhandled 500."""
    invalid_spec_json = VALID_SPEC_JSON.replace('"user_count": 50', '"user_count": 0')
    mock_client.messages.create.return_value = _fake_llm_response(invalid_spec_json)
    r = client.post("/design", json={"description": "empty office"})
    assert r.status_code == 422


_FAKE_PLAN = NetworkPlan(
    spec=NetworkSpec(org_name="Acme Dental", user_count=50),
    vlans=[],
    nodes=[],
    links=[],
)


@patch("pipeline.api.routes.generate_configs")
@patch("pipeline.api.routes.validate_plan")
@patch("pipeline.api.routes.generate_plan")
@patch("pipeline.llm_layer.client")
def test_design_returns_full_result_on_success(
    mock_llm_client, mock_generate_plan, mock_validate_plan, mock_generate_configs
):
    mock_llm_client.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
    mock_generate_plan.return_value = _FAKE_PLAN
    mock_validate_plan.return_value = ValidationReport(
        overall_pass=True,
        checks=[ValidationCheck(check_name="no_subnet_overlap", passed=True, detail="ok")],
    )
    mock_generate_configs.return_value = []

    r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 200
    assert r.json()["validation"]["overall_pass"] is True
    mock_generate_configs.assert_called_once()


@patch("pipeline.api.routes.generate_configs")
@patch("pipeline.api.routes.validate_plan")
@patch("pipeline.api.routes.generate_plan")
@patch("pipeline.llm_layer.client")
def test_design_skips_config_generation_when_validation_fails(
    mock_llm_client, mock_generate_plan, mock_validate_plan, mock_generate_configs
):
    mock_llm_client.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
    mock_generate_plan.return_value = _FAKE_PLAN
    mock_validate_plan.return_value = ValidationReport(
        overall_pass=False,
        checks=[ValidationCheck(check_name="no_subnet_overlap", passed=False, detail="overlap found")],
    )

    r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 200
    assert r.json()["validation"]["overall_pass"] is False
    assert r.json()["configs"] == []
    mock_generate_configs.assert_not_called()

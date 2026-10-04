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
  "department_segments": ["staff", "guest", "voip"],
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
    mock_client.beta.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
    r = client.post("/parse", json={"description": "50-person office"})
    assert r.status_code == 200
    assert r.json()["org_name"] == "Acme Dental"


@patch("pipeline.llm_layer.client")
def test_parse_returns_422_on_parse_error(mock_client):
    mock_client.beta.messages.create.return_value = _fake_llm_response("not json at all")
    r = client.post("/parse", json={"description": "50-person office"})
    assert r.status_code == 422


@patch("pipeline.llm_layer.client")
def test_parse_returns_503_on_service_error(mock_client):
    mock_client.beta.messages.create.side_effect = _rate_limit_error()
    r = client.post("/parse", json={"description": "50-person office"})
    assert r.status_code == 503


@patch("pipeline.llm_layer.client")
def test_design_returns_422_when_parsing_fails(mock_client):
    mock_client.beta.messages.create.return_value = _fake_llm_response("not json at all")
    r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 422


@patch("pipeline.llm_layer.client")
def test_design_returns_503_when_llm_service_fails(mock_client):
    mock_client.beta.messages.create.side_effect = _rate_limit_error()
    r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 503


@patch("pipeline.llm_layer.client")
def test_design_returns_real_result_end_to_end(mock_client):
    """Only the LLM is mocked here -- generate_plan/validate_plan/generate_configs
    are the real engine/ implementations."""
    mock_client.beta.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
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
    mock_client.beta.messages.create.return_value = _fake_llm_response(invalid_spec_json)
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
    mock_llm_client.beta.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
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
    mock_llm_client.beta.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
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


def _real_plan() -> dict:
    """A real generate_plan() output (dual WAN, isolated guest) via the mocked LLM."""
    with patch("pipeline.llm_layer.client") as mock_client:
        mock_client.beta.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
        r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 200
    return r.json()["plan"]


def test_demo_sabotages_lists_all_applicable_sabotages():
    plan = _real_plan()
    r = client.post("/demo/sabotages", json={"plan": plan})
    assert r.status_code == 200
    sabotages = r.json()
    assert len(sabotages) == 12
    assert {s["key"] for s in sabotages if s["kind"] == "design"} == {
        "overlapping_subnets", "public_subnet", "undersized_subnet",
        "duplicate_vlan_id", "missing_segment", "orphaned_switch",
        "missing_backup_wan", "firewall_bypass",
    }
    assert {s["key"] for s in sabotages if s["kind"] == "config"} == {
        "strip_guest_acl", "wrong_dhcp_gateway", "duplicate_ip", "drop_return_route",
    }


def test_demo_break_config_sabotage_is_caught_by_the_config_audit():
    plan = _real_plan()
    r = client.post("/demo/break", json={"plan": plan, "sabotage": "strip_guest_acl"})
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] == plan
    failed = {c["check_name"] for c in body["validation"]["checks"] if not c["passed"]}
    assert failed == {"guest_isolation_enforced"}


def test_demo_break_flips_only_its_target_check():
    plan = _real_plan()
    r = client.post("/demo/break", json={"plan": plan, "sabotage": "overlapping_subnets"})
    assert r.status_code == 200
    body = r.json()
    assert body["what_changed"]
    failed = {c["check_name"] for c in body["validation"]["checks"] if not c["passed"]}
    assert failed == {"no_subnet_overlap"}


def test_demo_break_returns_422_for_unknown_sabotage():
    plan = _real_plan()
    r = client.post("/demo/break", json={"plan": plan, "sabotage": "not_a_real_key"})
    assert r.status_code == 422


def test_demo_break_returns_422_for_inapplicable_sabotage():
    plan = _real_plan()
    plan["links"] = [l for l in plan["links"] if l["link_type"] != "redundant_wan"]
    r = client.post("/demo/break", json={"plan": plan, "sabotage": "missing_backup_wan"})
    assert r.status_code == 422


def _real_result() -> dict:
    with patch("pipeline.llm_layer.client") as mock_client:
        mock_client.beta.messages.create.return_value = _fake_llm_response(VALID_SPEC_JSON)
        r = client.post("/design", json={"description": "50-person office"})
    assert r.status_code == 200
    return r.json()


def test_simulate_reports_every_flow():
    plan = _real_plan()
    r = client.post("/simulate", json={"plan": plan})
    assert r.status_code == 200
    body = r.json()
    vlans = len(plan["vlans"])
    assert len(body["flows"]) == vlans * vlans  # every other VLAN + the internet
    guest_to_staff = next(f for f in body["flows"] if f["source"] == "guest" and f["destination"] == "staff")
    assert guest_to_staff["allowed"] is False


def test_simulate_with_failed_device_fails_over():
    r = client.post("/simulate", json={"plan": _real_plan(), "failed": ["isp-a"]})
    assert r.status_code == 200
    staff = next(f for f in r.json()["flows"] if f["source"] == "staff" and f["destination"] == "internet")
    assert staff["allowed"] and "isp-b" in staff["path"]


def test_simulate_returns_422_for_unknown_device():
    r = client.post("/simulate", json={"plan": _real_plan(), "failed": ["not-a-device"]})
    assert r.status_code == 422


def test_resilience_names_single_points_of_failure():
    r = client.post("/resilience", json={"plan": _real_plan()})
    assert r.status_code == 200
    assert "firewall1" in r.json()["single_points_of_failure"]


def test_parts_list():
    r = client.post("/parts", json={"plan": _real_plan()})
    assert r.status_code == 200
    parts = r.json()["parts"]
    assert all(p["look_for"] and p["example"] and p["why"] and p["quantity"] > 0 for p in parts)
    assert not any("cost" in key or "total" in key for p in parts for key in p)


@patch("pipeline.llm_layer.client")
def test_refine_redesigns_and_reports_changes(mock_client):
    plan = _real_plan()
    mock_client.beta.messages.create.return_value = _fake_llm_response(
        VALID_SPEC_JSON.replace('"user_count": 50', '"user_count": 150')
    )
    r = client.post("/refine", json={"plan": plan, "change": "make it 150 users"})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["plan"]["spec"]["user_count"] == 150
    assert "Users: 50 -> 150" in body["changes"]
    sent = mock_client.beta.messages.create.call_args.kwargs["messages"][0]["content"]
    assert '"user_count": 50' in sent and "make it 150 users" in sent


@pytest.mark.parametrize("change", ["", "   "])
def test_refine_rejects_blank_change(change):
    r = client.post("/refine", json={"plan": _real_plan(), "change": change})
    assert r.status_code == 422


@patch("pipeline.llm_layer.client")
def test_refine_returns_422_when_the_change_cant_be_applied(mock_client):
    plan = _real_plan()
    mock_client.beta.messages.create.return_value = _fake_llm_response("not json")
    r = client.post("/refine", json={"plan": plan, "change": "make it better"})
    assert r.status_code == 422


@patch("pipeline.llm_layer.client")
def test_explain_answers_from_the_design(mock_client):
    result = _real_result()
    mock_client.beta.messages.create.return_value = _fake_llm_response("Guests are blocked by GUEST-ISOLATION on core1.")
    r = client.post("/explain", json={"result": result, "question": "Can guests reach staff?"})
    assert r.status_code == 200
    assert "GUEST-ISOLATION" in r.json()["answer"]
    sent = mock_client.beta.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Can guests reach staff?" in sent
    assert "--- firewall1 ---" in sent and "--- access1 ---" not in sent


@patch("pipeline.llm_layer.client")
def test_explain_returns_503_when_llm_service_fails(mock_client):
    result = _real_result()
    mock_client.beta.messages.create.side_effect = [_rate_limit_error(), _rate_limit_error()]
    r = client.post("/explain", json={"result": result, "question": "Why a /26?"})
    assert r.status_code == 503


@patch("pipeline.llm_layer.client")
def test_samples_design_without_the_llm(mock_client):
    listing = client.get("/samples")
    assert listing.status_code == 200
    keys = [s["key"] for s in listing.json()]
    assert keys
    for key in keys:
        r = client.get(f"/samples/{key}")
        assert r.status_code == 200
        body = r.json()
        assert body["validation"]["overall_pass"] is True and body["configs"]
        assert body["plan"]["spec"]["assumptions"]
    mock_client.beta.messages.create.assert_not_called()


def test_unknown_sample_is_404():
    assert client.get("/samples/nope").status_code == 404


def test_fully_redundant_sample_has_no_single_point_of_failure():
    plan = client.get("/samples/vet_hospital").json()["plan"]
    assert client.post("/resilience", json={"plan": plan}).json()["single_points_of_failure"] == []

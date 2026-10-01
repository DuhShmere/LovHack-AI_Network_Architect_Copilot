"""Samir: tests for the LLM extraction layer against varied plain-English inputs."""

import os
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from pipeline.llm_layer import (
    RequirementParseError,
    RequirementServiceError,
    parse_requirements,
)

_FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _rate_limit_error() -> anthropic.RateLimitError:
    return anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_FAKE_REQUEST), body=None
    )


def _auth_error() -> anthropic.AuthenticationError:
    return anthropic.AuthenticationError(
        "invalid api key", response=httpx.Response(401, request=_FAKE_REQUEST), body=None
    )


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="requires a live ANTHROPIC_API_KEY"
)
def test_parses_basic_office_description():
    result = parse_requirements(
        "50-person office, guest wifi isolated, redundant WAN"
    )
    assert result.user_count == 50
    assert result.needs_guest_wifi is True
    assert result.guest_wifi_isolated is True


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


def _fake_response(text: str) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(type="text", text=text)]
    return response


@patch("pipeline.llm_layer.client")
def test_strips_json_code_fence(mock_client):
    mock_client.messages.create.return_value = _fake_response(
        f"```json\n{VALID_SPEC_JSON}\n```"
    )
    result = parse_requirements("50-person office")
    assert result.org_name == "Acme Dental"
    mock_client.messages.create.assert_called_once()


@patch("pipeline.llm_layer.client")
def test_finds_text_block_after_a_thinking_block(mock_client):
    """Extended thinking puts a ThinkingBlock (no .text) before the text block."""
    response = MagicMock()
    thinking_block = MagicMock(type="thinking")
    del thinking_block.text  # ThinkingBlock has no .text attribute
    response.content = [thinking_block, MagicMock(type="text", text=VALID_SPEC_JSON)]
    mock_client.messages.create.return_value = response

    result = parse_requirements("50-person office")
    assert result.org_name == "Acme Dental"


@pytest.mark.parametrize("blank", ['""', '"   "', "null"])
@patch("pipeline.llm_layer.client")
def test_blank_org_name_falls_back_to_default(mock_client, blank):
    """A blank org name produced SSIDs like '-STAFF' in the generated configs."""
    spec_json = VALID_SPEC_JSON.replace('"Acme Dental"', blank)
    mock_client.messages.create.return_value = _fake_response(spec_json)
    result = parse_requirements("50-person office")
    assert result.org_name == "Main Office"


@patch("pipeline.llm_layer.client")
def test_tolerates_surrounding_prose(mock_client):
    mock_client.messages.create.return_value = _fake_response(
        f"Sure, here's the JSON:\n{VALID_SPEC_JSON}\nHope that helps!"
    )
    result = parse_requirements("50-person office")
    assert result.user_count == 50


@patch("pipeline.llm_layer.client")
def test_retries_once_after_malformed_response_then_succeeds(mock_client):
    mock_client.messages.create.side_effect = [
        _fake_response("not json at all"),
        _fake_response(VALID_SPEC_JSON),
    ]
    result = parse_requirements("50-person office")
    assert result.org_name == "Acme Dental"
    assert mock_client.messages.create.call_count == 2


@patch("pipeline.llm_layer.client")
def test_raises_requirement_parse_error_after_repeated_failure(mock_client):
    mock_client.messages.create.return_value = _fake_response("not json at all")
    with pytest.raises(RequirementParseError):
        parse_requirements("50-person office")
    assert mock_client.messages.create.call_count == 2


@patch("pipeline.llm_layer.client")
def test_retries_once_after_rate_limit_then_succeeds(mock_client):
    mock_client.messages.create.side_effect = [
        _rate_limit_error(),
        _fake_response(VALID_SPEC_JSON),
    ]
    result = parse_requirements("50-person office")
    assert result.org_name == "Acme Dental"
    assert mock_client.messages.create.call_count == 2


@patch("pipeline.llm_layer.client")
def test_raises_requirement_service_error_after_repeated_rate_limit(mock_client):
    mock_client.messages.create.side_effect = [_rate_limit_error(), _rate_limit_error()]
    with pytest.raises(RequirementServiceError):
        parse_requirements("50-person office")
    assert mock_client.messages.create.call_count == 2


@patch("pipeline.llm_layer.client")
def test_raises_requirement_service_error_immediately_on_auth_failure(mock_client):
    mock_client.messages.create.side_effect = _auth_error()
    with pytest.raises(RequirementServiceError):
        parse_requirements("50-person office")
    assert mock_client.messages.create.call_count == 1


@patch("pipeline.llm_layer.client")
def test_assumptions_are_parsed(mock_client):
    mock_client.messages.create.return_value = _fake_response(
        VALID_SPEC_JSON.replace('"raw_notes": null', '"raw_notes": null, "assumptions": ["50 users = 45 staff + 5 contractors"]')
    )
    spec = parse_requirements("45 staff and 5 contractors")
    assert spec.assumptions == ["50 users = 45 staff + 5 contractors"]


@patch("pipeline.llm_layer.client")
def test_missing_assumptions_default_to_empty(mock_client):
    mock_client.messages.create.return_value = _fake_response(VALID_SPEC_JSON)
    assert parse_requirements("50-person office").assumptions == []


@patch("pipeline.llm_layer.client")
def test_refusal_is_retried_then_raises_parse_error(mock_client):
    response = _fake_response(VALID_SPEC_JSON)
    response.stop_reason = "refusal"
    mock_client.messages.create.return_value = response
    with pytest.raises(RequirementParseError, match="declined"):
        parse_requirements("50-person office")
    assert mock_client.messages.create.call_count == 2


@patch("pipeline.llm_layer.client")
def test_truncated_response_is_retried(mock_client):
    cut_off = _fake_response(VALID_SPEC_JSON[:40])
    cut_off.stop_reason = "max_tokens"
    mock_client.messages.create.side_effect = [cut_off, _fake_response(VALID_SPEC_JSON)]
    assert parse_requirements("50-person office").user_count > 0
    assert mock_client.messages.create.call_count == 2

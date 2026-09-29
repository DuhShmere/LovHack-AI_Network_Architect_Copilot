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

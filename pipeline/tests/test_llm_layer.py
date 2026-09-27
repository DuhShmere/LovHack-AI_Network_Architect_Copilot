"""Samir: tests for the LLM extraction layer against varied plain-English inputs."""

from unittest.mock import MagicMock, patch

import pytest

from pipeline.llm_layer import RequirementParseError, parse_requirements


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
    response.content = [MagicMock(text=text)]
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

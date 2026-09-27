"""Offline tests for requirement parsing and Anthropic API error handling."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from pipeline import llm_layer
from pipeline.llm_layer import (
    EXTRACTION_SYSTEM_PROMPT,
    LLMResponseError,
    MissingAPIKeyError,
    RequirementParseError,
    RequirementServiceError,
    get_client,
    parse_requirements,
    reset_client,
)
from shared.schema import RedundancyLevel

VALID_SPEC = {
    "org_name": "Acme Office",
    "user_count": 50,
    "needs_guest_wifi": True,
    "guest_wifi_isolated": True,
    "department_segments": ["staff", "guest"],
    "redundancy": "dual_wan",
    "preferred_base_cidr": None,
    "raw_notes": None,
}
FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def rate_limit_error():
    return anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=FAKE_REQUEST), body=None
    )


def auth_error():
    return anthropic.AuthenticationError(
        "invalid api key", response=httpx.Response(401, request=FAKE_REQUEST), body=None
    )


class FakeMessages:
    def __init__(self, responses):
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls = []

    def create(self, **kwargs):
        response_index = min(len(self.calls), len(self.responses) - 1)
        response = self.responses[response_index]
        self.calls.append(kwargs)
        if isinstance(response, BaseException):
            raise response
        blocks = (
            []
            if response is None
            else [SimpleNamespace(type="text", text=response)]
        )
        return SimpleNamespace(content=blocks)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


@pytest.fixture(autouse=True)
def fresh_client():
    reset_client()
    yield
    reset_client()


def test_parses_plain_json_and_sends_expected_request():
    client = FakeClient(json.dumps(VALID_SPEC))

    result = parse_requirements(
        "50-person office, guest wifi isolated, redundant WAN",
        client=client,
    )

    assert result.user_count == 50
    assert result.needs_guest_wifi is True
    assert result.guest_wifi_isolated is True
    assert result.redundancy is RedundancyLevel.dual_wan
    (call,) = client.messages.calls
    assert call["system"] == EXTRACTION_SYSTEM_PROMPT
    assert call["messages"] == [
        {
            "role": "user",
            "content": "50-person office, guest wifi isolated, redundant WAN",
        }
    ]


@pytest.mark.parametrize(
    "wrapper",
    [
        "```json\n{}\n```",
        "```\n{}\n```",
        "  ```json\n{}\n```  \n",
        "```JSON\n{}```",
    ],
)
def test_parses_fenced_json(wrapper):
    response = wrapper.replace("{}", json.dumps(VALID_SPEC, indent=2))

    result = parse_requirements("anything", client=FakeClient(response))

    assert result.org_name == "Acme Office"
    assert result.department_segments == ["staff", "guest"]


def test_parses_json_surrounded_by_prose():
    response = f"Here is the requested JSON:\n{json.dumps(VALID_SPEC)}\nHope this helps."

    result = parse_requirements("anything", client=FakeClient(response))

    assert result.org_name == "Acme Office"


def test_retries_once_after_malformed_response_then_succeeds():
    client = FakeClient(["not json", json.dumps(VALID_SPEC)])

    result = parse_requirements("anything", client=client)

    assert result.org_name == "Acme Office"
    assert len(client.messages.calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        "Sure! Here is the spec you asked for.",
        '{"org_name": "Acme", "user_count": 50',
        "```json\nnot json\n```",
        "",
    ],
)
def test_malformed_json_retries_then_raises(response):
    client = FakeClient(response)

    with pytest.raises(RequirementParseError, match="not valid JSON"):
        parse_requirements("anything", client=client)

    assert len(client.messages.calls) == 2


def test_non_object_json_raises_parse_error():
    with pytest.raises(RequirementParseError, match="Expected a JSON object"):
        parse_requirements("anything", client=FakeClient("[1, 2, 3]"))


def test_schema_mismatch_retries_then_raises():
    bad_spec = {**VALID_SPEC, "user_count": "lots", "redundancy": "triple_wan"}

    with pytest.raises(RequirementParseError, match="did not match NetworkSpec"):
        parse_requirements("anything", client=FakeClient(json.dumps(bad_spec)))


def test_missing_required_field_retries_then_raises():
    bad_spec = {key: value for key, value in VALID_SPEC.items() if key != "org_name"}

    with pytest.raises(RequirementParseError, match="did not match NetworkSpec"):
        parse_requirements("anything", client=FakeClient(json.dumps(bad_spec)))


def test_no_text_content_raises():
    with pytest.raises(LLMResponseError, match="no text content"):
        parse_requirements("anything", client=FakeClient(None))


def test_retries_once_after_rate_limit_then_succeeds():
    client = FakeClient([rate_limit_error(), json.dumps(VALID_SPEC)])

    result = parse_requirements("anything", client=client)

    assert result.org_name == "Acme Office"
    assert len(client.messages.calls) == 2


def test_repeated_rate_limit_raises_service_error():
    client = FakeClient([rate_limit_error(), rate_limit_error()])

    with pytest.raises(RequirementServiceError):
        parse_requirements("anything", client=client)

    assert len(client.messages.calls) == 2


def test_authentication_failure_raises_service_error_without_retry():
    client = FakeClient(auth_error())

    with pytest.raises(RequirementServiceError):
        parse_requirements("anything", client=client)

    assert len(client.messages.calls) == 1


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_api_key_raises_clear_error(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)

    with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY is not set"):
        parse_requirements("anything")


def test_client_created_lazily_and_cached(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    created = []

    def fake_anthropic(**kwargs):
        created.append(kwargs)
        return FakeClient(json.dumps(VALID_SPEC))

    monkeypatch.setattr(llm_layer.anthropic, "Anthropic", fake_anthropic)

    assert get_client() is get_client()
    assert created == [{"api_key": "test-key-not-real"}]
    assert parse_requirements("anything").user_count == 50


def test_error_message_does_not_leak_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")

    with pytest.raises(RequirementParseError) as exc:
        parse_requirements("anything", client=FakeClient("garbage"))

    assert "sk-secret-value" not in str(exc.value)


def test_env_file_points_at_project_root():
    assert llm_layer.ENV_FILE.name == ".env"
    assert (llm_layer.ENV_FILE.parent / "pipeline" / "llm_layer.py").is_file()

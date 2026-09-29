"""
LLM layer: plain-English requirements -> structured NetworkSpec.

Owner: Samir. Day 2 task. Works standalone against the Anthropic API
without depending on engine/ at all.
"""

import json
import os

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from shared.schema import NetworkSpec

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-sonnet-5"

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured network requirements from a plain-English description.
Respond ONLY with a JSON object matching this shape, nothing else:

{
  "org_name": string,
  "user_count": integer,
  "needs_guest_wifi": boolean,
  "guest_wifi_isolated": boolean,
  "department_segments": [string, ...],
  "redundancy": "none" | "dual_wan" | "dual_wan_plus_switch_redundancy",
  "preferred_base_cidr": string or null,
  "raw_notes": string or null
}
"""


class RequirementParseError(Exception):
    """Raised when a plain-English description can't be parsed into a NetworkSpec."""


class RequirementServiceError(Exception):
    """Raised when the Anthropic API itself fails (auth, rate limit, connection, timeout, server error)."""


# Transient failures worth retrying once; auth/bad-request errors won't
# succeed on a second try, so those fail fast instead.
_RETRYABLE_API_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,  # also covers APITimeoutError
    anthropic.InternalServerError,
    anthropic.OverloadedError,
)


def _extract_json(text: str) -> str:
    """Pull a JSON object out of a model response, tolerating code fences and prose."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline != -1 else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in model response: {text!r}")
    return text[start : end + 1]


def _call_model(plain_english: str) -> NetworkSpec:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": plain_english}],
    )
    # content[0] isn't always the text block -- extended thinking, when the
    # model uses it, puts a ThinkingBlock first.
    text_block = next((block for block in response.content if block.type == "text"), None)
    if text_block is None:
        raise ValueError("model response contained no text block")
    json_text = _extract_json(text_block.text)
    data = json.loads(json_text)
    return NetworkSpec(**data)


def parse_requirements(plain_english: str) -> NetworkSpec:
    """
    Parse a plain-English network description into a structured NetworkSpec.

    Retries once against the model if the first response can't be parsed
    (malformed JSON, or JSON that doesn't validate against NetworkSpec) before
    giving up and raising RequirementParseError. Transient Anthropic API
    failures (rate limits, connection issues, server errors) are also
    retried once and then raised as RequirementServiceError; non-retryable
    API errors (bad auth, bad request) raise RequirementServiceError
    immediately.
    """
    last_error: Exception | None = None
    for _ in range(2):
        try:
            return _call_model(plain_english)
        except (json.JSONDecodeError, ValidationError, ValueError, IndexError) as e:
            last_error = e
        except _RETRYABLE_API_ERRORS as e:
            last_error = e
        except anthropic.APIError as e:
            raise RequirementServiceError(f"Anthropic API request failed: {e}") from e

    if isinstance(last_error, _RETRYABLE_API_ERRORS):
        raise RequirementServiceError(
            f"Anthropic API unavailable after retrying: {last_error}"
        ) from last_error

    raise RequirementParseError(
        f"Could not extract a valid NetworkSpec after retrying: {last_error}"
    ) from last_error

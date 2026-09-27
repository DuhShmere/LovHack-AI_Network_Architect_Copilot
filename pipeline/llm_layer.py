"""
LLM layer: plain-English requirements -> structured NetworkSpec.

Owner: Samir. Works standalone against the Anthropic API without depending
on the engine at all.

The Anthropic client is created lazily (so importing this module never needs
an API key) and can be injected, which is how the tests mock it:

    parse_requirements("...", client=fake_client)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from shared.schema import NetworkSpec

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Values already set in the real environment win over the .env file.
load_dotenv(ENV_FILE, override=False)

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1000

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


class MissingAPIKeyError(RuntimeError):
    """ANTHROPIC_API_KEY is not set in the environment or the project .env."""


class LLMResponseError(ValueError):
    """The model's response could not be turned into a valid NetworkSpec."""


class RequirementParseError(LLMResponseError):
    """The model response remained invalid after one retry."""


class RequirementServiceError(RuntimeError):
    """The Anthropic service could not process the request."""


_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    """Return the shared Anthropic client, creating it on first use."""
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Add it to the environment or to "
                f"{ENV_FILE} (see .env.example)."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def reset_client() -> None:
    """Drop the cached client (e.g. after the API key changes, or in tests)."""
    global _client
    _client = None


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text


# Transient failures worth retrying once; auth/bad-request errors won't
# succeed on a second try, so those fail fast instead.
_RETRYABLE_API_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,  # also covers APITimeoutError
    anthropic.InternalServerError,
    anthropic.OverloadedError,
)


def _extract_json(text: str) -> str:
    """Extract a JSON object from plain output, code fences, or surrounding prose."""
    text = _strip_fences(text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start:end + 1]


def _response_text(response: Any) -> str:
    parts = [
        block.text
        for block in getattr(response, "content", None) or []
        if getattr(block, "type", None) == "text"
    ]
    if not parts:
        raise LLMResponseError("Model response contained no text content.")
    return "".join(parts)


def _preview(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def parse_requirements(
    plain_english: str, client: Optional[anthropic.Anthropic] = None
) -> NetworkSpec:
    """
    Extract a NetworkSpec from a plain-English description.

    Malformed model responses and transient service failures are retried once.
    Missing keys, persistent parsing failures, and service failures are reported
    with distinct exceptions so the API can return useful HTTP status codes.
    """
    client = client or get_client()
    last_error = None
    for _ in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": plain_english}],
            )
            text = _response_text(response)
            data = json.loads(_extract_json(text))
            if not isinstance(data, dict):
                raise LLMResponseError(
                    f"Expected a JSON object, got {type(data).__name__}: {_preview(text)!r}"
                )
            return NetworkSpec(**data)
        except json.JSONDecodeError as e:
            last_error = LLMResponseError(
                f"Model response was not valid JSON ({e.msg}): {_preview(text)!r}"
            )
        except ValidationError as e:
            last_error = LLMResponseError(
                f"Model response did not match NetworkSpec: {e}"
            )
        except LLMResponseError as e:
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

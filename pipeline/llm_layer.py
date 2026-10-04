"""
LLM layer: plain-English requirements -> structured NetworkSpec.

Owner: Samir. Works standalone against the Anthropic API without
depending on engine/ at all.

Also: refine_requirements() applies a plain-English change to an existing
spec, and explain_design() answers questions about a finished design.
"""

import json
import os

import anthropic
from dotenv import load_dotenv
from pydantic import ValidationError

from shared.schema import FullResult, NetworkSpec

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-sonnet-5"

# Shared by every prompt that returns a spec, so the allowed values (the
# redundancy enum especially) are always spelled out.
SPEC_SHAPE = """\
{
  "org_name": string,
  "user_count": integer,
  "needs_guest_wifi": boolean,
  "guest_wifi_isolated": boolean,
  "department_segments": [string, ...],
  "redundancy": "none" | "dual_wan" | "dual_wan_plus_switch_redundancy",
  "preferred_base_cidr": string or null,
  "raw_notes": string or null,
  "assumptions": [string, ...]
}"""

# Segment names become VLAN names, and Cisco caps those at 32 characters.
SEGMENT_NAMING = """\
Each entry in "department_segments" is a short name of 1-3 words, at most 20
characters (e.g. "clinical", "front desk", "iot"). Put details such as which
devices a segment holds in "assumptions", never in the name.
"""

EXTRACTION_SYSTEM_PROMPT = f"""\
You extract structured network requirements from a plain-English description.
Respond ONLY with a JSON object matching this shape, nothing else:

{SPEC_SHAPE}

{SEGMENT_NAMING}
If the description doesn't name the organization, use a short descriptive
name based on what it is (e.g. "Dental Clinic", "Main Office").

In "assumptions", list each value you inferred, calculated or defaulted
rather than read directly, with the reasoning in one short sentence, e.g.
"user_count 264 = 214 employees + 38 contractors + 12 interns; 40 remote
staff excluded". Use [] if everything was stated outright.

Short requests like "50-person office" are fine: default what's missing and
list it in "assumptions". Only if the text isn't asking for a network at all
(random words, an unrelated question), respond instead with
{{"not_a_network": "<one short sentence saying why>"}}.
"""

REFINE_SYSTEM_PROMPT = f"""\
You update structured network requirements. You are given the current
requirements as JSON and a requested change in plain English.

Respond ONLY with the complete updated JSON object, nothing else, matching
this shape -- enum fields must use one of the listed values exactly (a
single internet connection is "none"):

{SPEC_SHAPE}

{SEGMENT_NAMING}
Change only what the request asks for, plus anything it directly implies.
In "assumptions", list what you inferred for this change, and keep earlier
assumptions that still hold. If the request asks for something this shape
can't express, leave the fields as they are and say so in "raw_notes".
"""

EXPLAIN_SYSTEM_PROMPT = """\
You are a network engineer explaining a network design to the person who
asked for it. The design was generated and validated by deterministic code.
You are given its requirements, VLAN plan, topology, validation report and
the configs of its routing devices (access switch and AP configs are left
out; they are repetitive port and SSID settings).

Answer the question using only that material, and point to the specific
VLANs, subnets, devices, config lines or checks involved. If the material
doesn't answer the question, say so plainly instead of guessing. Under 200
words of plain text: the page shows it as-is, so no Markdown (no **bold**,
no # headings); separate paragraphs with a blank line.
"""

ROUTING_DEVICE_TYPES = {"router", "firewall", "core_switch"}

# Device configs bake the org name into SSIDs and banners, so it can't be blank.
DEFAULT_ORG_NAME = "Main Office"


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


def _ask(system: str, content: str, max_tokens: int) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise ValueError("model declined the request")
    if response.stop_reason == "max_tokens":
        raise ValueError("model response was cut off at max_tokens")
    # content[0] isn't always the text block -- extended thinking, when the
    # model uses it, puts a ThinkingBlock first.
    text_block = next((block for block in response.content if block.type == "text"), None)
    if text_block is None:
        raise ValueError("model response contained no text block")
    return text_block.text


def _spec_from_text(text: str) -> NetworkSpec:
    data = json.loads(_extract_json(text))
    if "not_a_network" in data:
        # Not retried: asking again won't turn "banana" into a network.
        reason = str(data["not_a_network"]).strip().rstrip(".")
        raise RequirementParseError(
            f"That doesn't look like a network request ({reason}). "
            "Try something like \"50-person office, guest Wi-Fi isolated, two internet providers\"."
        )
    if not str(data.get("org_name") or "").strip():
        data["org_name"] = DEFAULT_ORG_NAME
    return NetworkSpec(**data)


def _call_model(plain_english: str) -> NetworkSpec:
    return _spec_from_text(_ask(EXTRACTION_SYSTEM_PROMPT, plain_english, max_tokens=4000))


def _with_retries(call, what: str):
    """Run call(), retrying once on a bad model response or a transient API
    failure. Raises RequirementParseError / RequirementServiceError."""
    last_error: Exception | None = None
    for _ in range(2):
        try:
            return call()
        except (json.JSONDecodeError, ValidationError, ValueError, IndexError) as e:
            last_error = e
        except _RETRYABLE_API_ERRORS as e:
            last_error = e
        except anthropic.AuthenticationError as e:
            raise RequirementServiceError(
                "No valid Anthropic API key. Set ANTHROPIC_API_KEY in .env and restart "
                "the server, or try a sample design, which needs no key."
            ) from e
        except anthropic.APIError as e:
            raise RequirementServiceError(f"Anthropic API request failed: {e}") from e

    if isinstance(last_error, _RETRYABLE_API_ERRORS):
        raise RequirementServiceError(
            f"Anthropic API unavailable after retrying: {last_error}"
        ) from last_error

    raise RequirementParseError(f"{what} after retrying: {last_error}") from last_error


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
    return _with_retries(
        lambda: _call_model(plain_english), "Could not extract a valid NetworkSpec"
    )


def refine_requirements(spec: NetworkSpec, change: str) -> NetworkSpec:
    """Apply a plain-English change ("make it 150 users") to an existing spec.

    Same retry and error behavior as parse_requirements().
    """
    content = (
        f"Current requirements:\n{spec.model_dump_json(indent=2)}\n\n"
        f"Requested change:\n{change}"
    )
    return _with_retries(
        lambda: _spec_from_text(_ask(REFINE_SYSTEM_PROMPT, content, max_tokens=4000)),
        "Could not apply the change to the requirements",
    )


def explain_design(result: FullResult, question: str) -> str:
    """Answer a question about a finished design, grounded in the design itself.

    Same retry and error behavior as parse_requirements().
    """
    routing_ids = {n.node_id for n in result.plan.nodes if n.node_type in ROUTING_DEVICE_TYPES}
    configs = "\n\n".join(
        f"--- {c.node_id} ---\n{c.config_text}" for c in result.configs if c.node_id in routing_ids
    )
    content = (
        f"Design (requirements, VLANs, topology):\n{result.plan.model_dump_json(indent=1)}\n\n"
        f"Validation report:\n{result.validation.model_dump_json(indent=1)}\n\n"
        f"Routing device configs:\n{configs or '(none generated)'}\n\n"
        f"Question:\n{question}"
    )

    def ask() -> str:
        answer = _ask(EXPLAIN_SYSTEM_PROMPT, content, max_tokens=4000).strip()
        if not answer:
            raise ValueError("model returned an empty answer")
        return answer

    return _with_retries(ask, "Could not get an answer")

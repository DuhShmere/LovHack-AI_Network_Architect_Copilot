"""
LLM layer: plain-English requirements -> structured NetworkSpec.

Owner: Samir. Day 2 task. Stub the call to engine.generator until Nyles's
generate_plan() is ready (Day 3) -- this file should work standalone against
the Anthropic API without depending on the engine at all.
"""

import os
import json
import anthropic
from shared.schema import NetworkSpec

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

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


def parse_requirements(plain_english: str) -> NetworkSpec:
    """
    TODO (Samir):
      - Call the Anthropic API with EXTRACTION_SYSTEM_PROMPT.
      - Parse the JSON response (strip ```json fences defensively).
      - Validate it against NetworkSpec (pydantic will raise if malformed --
        catch that and either retry once or surface a clear error).
    """
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": plain_english}],
    )
    text = response.content[0].text.strip()
    text = text.removeprefix("```json").removesuffix("```").strip()
    data = json.loads(text)
    return NetworkSpec(**data)

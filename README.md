# AI Network Architect Copilot

Plain-English network requirements → topology diagram, IP/VLAN plan, Cisco-style
config, and a **validation report** proving the design is actually correct.

Built for LovHack Season 3 (Sept 26 – Oct 4, 2026).

## Current status

- **`pipeline/llm_layer.py`** — done. `parse_requirements()` calls the
  Anthropic API, extracts JSON robustly (handles code fences and surrounding
  prose), retries once on a malformed/invalid response before raising
  `RequirementParseError`, and separately handles transient API failures
  (rate limits, connection errors, server overload — retried once, then
  `RequirementServiceError`) vs. non-retryable ones (bad auth/request — raised
  immediately). The spec now carries `assumptions` (what the LLM inferred
  rather than read). `refine_requirements()` applies a plain-English change
  to a spec; `explain_design()` answers questions grounded in a design and
  its routing configs.
- **`pipeline/api/routes.py`** — `POST /parse` (LLM extraction only, no
  `engine/` dependency) and `POST /design` (full pipeline) are wired up, plus
  `/refine` (redesign + what changed), `/explain`, `/simulate`, `/resilience`
  and `/bom`.
  `RequirementParseError` → 422, `RequirementServiceError` → 503,
  `engine.generator.PlanGenerationError` (bad CIDR, 0 users, etc.) → 422.
  Blank/empty `description` is rejected at the request-validation layer.
- **`engine/`** — done (Nyles). `generator.py` allocates non-overlapping VLAN
  subnets and scales the topology to `user_count` (1 access switch per 48
  users, 1 AP per 25; redundant WAN/core switches when requested).
  `validator.py` runs 8 concrete checks (valid ranges, no subnet overlap,
  valid VLAN IDs, required segments present, subnet capacity, topology
  connectivity, redundancy present, guest isolation). `config_gen.py` emits
  Cisco IOS-style config per device (skips ISP `wan_uplink` nodes, and skips
  all configs if validation fails). Once the design passes, `config_audit.py`
  re-reads those generated configs and adds up to 4 more checks: guest ACLs
  evaluated rule by rule, gateway/HSRP/DHCP agreement, no IP conflicts, and
  static routes traced hop by hop (including failover routes).
  `simulator.py` walks traffic through the generated configs (ACLs, routes,
  HSRP, IP SLA tracking, NAT, return path) for a VLAN reachability matrix
  under any set of failed devices, and tries every single-device failure to
  find single points of failure (a fully redundant design, with its firewall
  pair, has none). `demo.py` breaks a design -- or just its configs -- one
  named way for the live demo. `samples.py` holds ready-made specs that
  skip the LLM (`GET /samples`), for demos with no API access. `bom.py` prices
  the topology with budgetary figures; `plan_diff.py` describes what a
  refinement changed.
- **`/design` is fully live end-to-end** — plain English in, a real validated
  `FullResult` out.
- **`pipeline/tests/`** — route-level tests for `/health`, `/parse`, `/design`
  (including a real end-to-end run against the live engine, mocking only the
  LLM), plus mocked unit tests for the LLM layer's retry/error-handling logic
  and JSON extraction (including a regression test for a real bug: extended
  thinking puts a `ThinkingBlock` before the text block in the API response).
- **`dashboard/`** — plain HTML/CSS/JS in `dashboard/static/`, served by
  FastAPI at `/`. Shows what was understood (with assumptions) and a refine
  box, the validation report, break-it demo, topology, failure simulator and
  reachability matrix (click a cell to trace its path on the topology),
  addressing, bill of materials, configs (one at a time or all as a .zip),
  and Q&A about the design.

## Team ownership (this is the whole point of the folder layout)

| Folder | Owner | Contains |
|---|---|---|
| `engine/` | **Nyles** | Requirement taxonomy, generator (spec → IP/VLAN/topology), validator, Cisco config generator, simulator stretch goal |
| `pipeline/` | **Samir** | LLM layer (plain English → structured spec), FastAPI backend, integration/testing |
| `dashboard/` | **Samir** | ProjectAAL dashboard (input form, results panel, diagram rendering) |
| `shared/schema.py` | **Both — agree Day 1, touch rarely after** | The Pydantic models that define the JSON contract between engine and pipeline |
| `docs/spec_schema.md` | **Both** | Human-readable description of that same contract |

If you're only ever editing inside your own folder, you will almost never get
a merge conflict. The only file that needs a real conversation before editing
is `shared/schema.py`.

## Setup

Needs **Python 3.10 or newer** (check with `python3 --version`). The
`python3` built into macOS is 3.9, which installs fine but crashes on
startup; get a newer one from [python.org](https://www.python.org/downloads/)
or with `brew install python`.

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in your Anthropic API key
uvicorn pipeline.api.main:app --reload
```

Then open http://127.0.0.1:8000/. No API key? The **sample design** buttons
under the input box run the full engine without one.

## Git workflow

- `main` is always demo-able. Don't push broken code directly to it.
- Branch names signal ownership so it's obvious who's touching what:
  - Nyles: `engine/generator`, `engine/validator`, `engine/config-gen`, `engine/simulator`
  - Samir: `pipeline/llm-layer`, `pipeline/api`, `dashboard/results-panel`
- Small, frequent PRs beat one giant end-of-day PR. Merge into `main` as soon
  as a piece works, even half-finished, behind a stub.
- Both of you only import from `shared/`, never reach into each other's folder
  directly. The FastAPI layer in `pipeline/api/` is the only place that calls
  into `engine/` — that's the single integration seam, and it's explicitly
  Samir's job per the schedule (Day 3–4).

## Day-by-day map (see docs/spec_schema.md for the contract you lock in on Day 1)

- **Sep 26**: Agree on `shared/schema.py` together. Then split — Nyles starts
  `engine/taxonomy.py`, Samir sets up `pipeline/api/main.py` skeleton + ProjectAAL project.
- **Sep 27**: Nyles builds `engine/generator.py` against the schema (no API
  needed — pure Python, testable standalone). Samir builds `pipeline/llm_layer.py`,
  stubbing calls to `engine.generator` until it's ready.
- **Sep 28**: Nyles finishes the generator function. Samir wires real LLM
  output into it via the API, adds error handling.
- **Sep 29**: Nyles builds `engine/validator.py`. Samir integrates validator
  output into the API response shape, writes varied test cases.
- **Sep 30**: Nyles builds `engine/config_gen.py` + stretch: containerlab sim.
  Samir wires the dashboard skeleton to the backend.
- **Oct 1**: Nyles polishes config/sim, helps debug integration. Samir finishes
  the dashboard.
- **Oct 2**: End-to-end integration testing, together.
- **Oct 3–4**: Devpost writeup, demo video, buffer.

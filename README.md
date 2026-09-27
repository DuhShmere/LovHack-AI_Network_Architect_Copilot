# AI Network Architect Copilot

Plain-English network requirements → topology diagram, IP/VLAN plan, Cisco-style
config, and a **validation report** proving the design is actually correct.

Built for LovHack Season 3 (Sept 26 – Oct 4, 2026).

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

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in your Anthropic API key
uvicorn pipeline.api.main:app --reload
```

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

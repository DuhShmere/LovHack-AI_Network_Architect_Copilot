# Dashboard

Owner: Samir. Plain HTML/CSS/JS, no build step -- served directly by FastAPI
(`pipeline/api/main.py` mounts `dashboard/static/` at `/`).

## Run it

```bash
uvicorn pipeline.api.main:app --reload
```

Then open http://127.0.0.1:8000/ in a browser.

## What it renders

- Input form (free-text description), plus sample designs that skip the LLM
  (`GET /samples`) so the demo works with no API key or network
- What we understood: the parsed spec, the LLM's assumptions, and a box to
  change the design in plain English (`POST /refine`, with a diff)
- Validation report (design checks plus the config audit -- the demo moment)
- Break it: sabotage the design or its configs and watch one check go red
- Topology diagram (hand-drawn SVG, layered by node type: WAN uplinks ->
  routers -> firewalls -> core switches -> access switches -> APs)
- Simulate: fail devices and see VLAN reachability (`POST /simulate`), plus
  single points of failure (`POST /resilience`); click a cell to trace its
  path on the topology
- VLAN/IP allocation table and bill of materials (`POST /bom`)
- Config downloads, one device at a time or all as a .zip (skips ISP
  `wan_uplink` nodes, which don't get a config -- that's the ISP's gear)
- Ask about this design (`POST /explain`)

## Files

- `static/index.html` -- markup
- `static/style.css` -- styling
- `static/app.js` -- calls the API, renders every panel, handles the
  422/503 error paths
- `fixtures/sample_full_result.json` -- the real `/samples/vet_hospital`
  response (a fully redundant design), as a schema reference

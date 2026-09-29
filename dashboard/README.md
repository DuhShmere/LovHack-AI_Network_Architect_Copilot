# Dashboard

Owner: Samir. Plain HTML/CSS/JS, no build step -- served directly by FastAPI
(`pipeline/api/main.py` mounts `dashboard/static/` at `/`). No ProjectAAL
account or setup needed.

## Run it

```bash
uvicorn pipeline.api.main:app --reload
```

Then open http://127.0.0.1:8000/ in a browser.

## What it renders, from a call to `POST /design`

- Input form (free-text description)
- Validation report (pass/fail per check -- this is the demo moment)
- Topology diagram (hand-drawn SVG, layered by node type: WAN uplinks ->
  routers -> firewall -> core switches -> access switches -> APs)
- VLAN/IP allocation table
- Config download button per device (skips ISP `wan_uplink` nodes, which
  don't get a config -- that's the ISP's gear, not ours)

## Files

- `static/index.html` -- markup
- `static/style.css` -- styling
- `static/app.js` -- fetches `/design`, renders all four panels, handles the
  422/503 error paths from the API
- `fixtures/sample_full_result.json` -- a hand-built `FullResult` from before
  `engine/` was real. Predates the real engine's output in a few ways (4
  checks instead of 8, a `/28` management subnet instead of `/27`) -- useful
  as a schema reference, not as ground truth for what the API returns today.

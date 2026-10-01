# The Spec Schema Contract

Read this together on Day 1 before writing any code. This describes, in
plain language, the JSON shape that flows between the two halves of the
project. The actual enforced version lives in `shared/schema.py` --
this doc exists so you can talk about it without both staring at Python.

## 1. NetworkSpec (LLM layer output -> generator input)

The structured version of a plain-English request like:
"50-person office, guest wifi isolated, redundant WAN"

| Field | Type | Meaning |
|---|---|---|
| org_name | string | Name of the org being designed for |
| user_count | int | Total people needing network access |
| needs_guest_wifi | bool | Is there a guest network at all |
| guest_wifi_isolated | bool | Must guest be isolated from internal traffic |
| department_segments | list[string] | e.g. ["staff", "admin", "iot"] |
| redundancy | enum | "none" / "dual_wan" / "dual_wan_plus_switch_redundancy" |
| preferred_base_cidr | string or null | e.g. "10.0.0.0/16" if specified |
| raw_notes | string or null | Anything else the LLM couldn't structure |
| assumptions | list[string] | What the LLM inferred or defaulted rather than read directly (defaults to []; engine ignores it) |

## 2. NetworkPlan (generator output -> validator + config gen + dashboard input)

- `vlans`: list of {vlan_id, name, subnet_cidr, purpose}
- `nodes`: list of {node_id, node_type, label} -- routers, switches, APs
- `links`: list of {source_id, target_id, link_type}

## 3. ValidationReport (validator output -> dashboard, the demo moment)

- `overall_pass`: bool
- `checks`: list of {check_name, passed, detail} -- e.g.
  "no_subnet_overlap: PASS -- all 4 VLAN subnets are disjoint"

## 4. DeviceConfig (config generator output -> dashboard download button)

- list of {node_id, config_text} -- one Cisco-style config block per device

## Changing this contract

If mid-hackathon you realize a field is missing or wrong: stop, message
each other, agree on the change together, then one person edits
`shared/schema.py` and pushes immediately so the other isn't working
against a stale version.

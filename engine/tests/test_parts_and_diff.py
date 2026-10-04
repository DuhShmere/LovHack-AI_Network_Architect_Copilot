"""Nyles: parts list and plan diff tests."""

import re
from collections import Counter

from shared.schema import NetworkSpec, RedundancyLevel
from engine.generator import generate_plan
from engine.parts_list import parts_list
from engine.plan_diff import diff_plans


def _plan(**overrides):
    fields = dict(
        org_name="Acme Dental",
        user_count=120,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["staff", "iot"],
        redundancy=RedundancyLevel.dual_wan_plus_switch_redundancy,
    )
    fields.update(overrides)
    return generate_plan(NetworkSpec(**fields))


def _by_name(plan):
    return {p.name: p for p in parts_list(plan).parts}


def test_quantities_match_the_topology():
    plan = _plan()
    parts = _by_name(plan)
    counts = Counter(n.node_type for n in plan.nodes)
    assert parts["Edge router"].quantity == counts["router"] == 2
    assert parts["Firewall"].quantity == counts["firewall"] == 2
    assert parts["Layer 3 core switch"].quantity == counts["core_switch"] == 2
    assert parts["48-port PoE+ access switch"].quantity == counts["access_switch"]
    assert parts["Wi-Fi 6 access point"].quantity == counts["ap"]
    drops = 120 + counts["ap"]
    assert parts["Cat6 cable drop, installed"].quantity == drops
    assert parts["Cat6 patch cable"].quantity == drops + len(plan.links)
    assert parts["48-port Cat6 patch panel"].quantity == -(-drops // 48)
    assert parts["Business internet circuit"].quantity == 2


def test_every_part_says_what_to_look_for_and_why():
    for part in parts_list(_plan()).parts:
        assert part.look_for and part.example and part.why, part.name


def test_redundancy_doubles_the_edge():
    full, single = _by_name(_plan()), _by_name(_plan(redundancy=RedundancyLevel.none))
    for name in ("Edge router", "Firewall", "Layer 3 core switch", "Business internet circuit"):
        assert full[name].quantity == 2 * single[name].quantity, name


def test_core_spec_counts_its_ports_in_use():
    plan = _plan()
    core1_ports = sum("core1" in (l.source_id, l.target_id) for l in plan.links)
    assert f"{core1_ports} Gigabit ports in use" in _by_name(plan)["Layer 3 core switch"].look_for


def test_a_core_with_more_links_than_ports_is_bought_as_a_stack():
    plan = _plan(user_count=3000, redundancy=RedundancyLevel.none)
    core = _by_name(plan)["Layer 3 core switch"]
    used = int(re.search(r"(\d+) Gigabit ports in use", core.look_for).group(1))
    assert used > 48 and core.quantity == -(-used // 48)
    assert "stack" in core.look_for


def test_poe_budget_covers_aps_and_phones_only_with_voip():
    without = _by_name(_plan())["48-port PoE+ access switch"]
    with_voip = _by_name(_plan(department_segments=["staff", "iot", "voip"]))["48-port PoE+ access switch"]
    watts = lambda p: int(re.search(r"at least (\d+) W", p.look_for).group(1))
    assert watts(with_voip) > watts(without) > 0
    assert "voice VLAN" in with_voip.look_for and "voice VLAN" not in without.look_for


def test_small_site_gets_a_wall_rack_and_one_ups():
    parts = _by_name(_plan(user_count=10, redundancy=RedundancyLevel.none))
    assert parts["12U wall-mount rack"].quantity == 1
    assert parts["Rack-mount UPS"].quantity == 1


def test_full_redundancy_gets_a_ups_per_power_feed():
    parts = _by_name(_plan())
    racks = sum(p.quantity for name, p in parts.items() if "rack" in name and "UPS" not in name)
    assert parts["Rack-mount UPS"].quantity == 2 * racks


def test_circuit_is_sized_for_the_headcount():
    assert "250 Mbps" in _by_name(_plan())["Business internet circuit"].look_for  # 120 users
    assert "1 Gbps" in _by_name(_plan(user_count=400))["Business internet circuit"].look_for


def test_diff_of_identical_plans():
    assert diff_plans(_plan(), _plan()) == ["No change to the design."]


def test_diff_reports_spec_vlan_and_device_changes():
    old = _plan()
    new = _plan(user_count=300, department_segments=["staff", "iot", "voip"], redundancy=RedundancyLevel.dual_wan)
    changes = diff_plans(old, new)
    assert "Users: 120 -> 300" in changes
    assert "Redundancy: dual_wan_plus_switch_redundancy -> dual_wan" in changes
    assert any(c.startswith("Added VLAN 40 'voip'") for c in changes)
    assert any(c.startswith("VLAN 10 'staff' readdressed") for c in changes)
    assert "Core switches: 2 -> 1" in changes
    assert any(c.startswith("Access switches: 3 -> ") for c in changes)


def test_diff_keeps_acronyms_in_device_names():
    changes = diff_plans(_plan(), _plan(redundancy=RedundancyLevel.none))
    assert "ISP circuits: 2 -> 1" in changes

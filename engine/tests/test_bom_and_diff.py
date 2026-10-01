"""Nyles: BOM and plan diff tests."""

from collections import Counter

from shared.schema import NetworkSpec, RedundancyLevel
from engine.generator import generate_plan
from engine.bom import bill_of_materials
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


def test_bom_quantities_match_the_topology():
    plan = _plan()
    bom = bill_of_materials(plan)
    counts = Counter(n.node_type for n in plan.nodes)
    by_item = {l.item: l for l in bom.lines}
    assert by_item["Layer 3 core switch"].quantity == counts["core_switch"] == 2
    assert by_item["48-port PoE+ access switch"].quantity == counts["access_switch"]
    assert by_item["Wi-Fi 6 access point"].quantity == counts["ap"]
    assert by_item["Cat6 cable drop, installed"].quantity == 120 + counts["ap"]
    circuits = by_item["Business internet circuit"]
    assert circuits.recurring and circuits.quantity == 2


def test_bom_totals_split_one_time_and_monthly():
    bom = bill_of_materials(_plan())
    assert bom.one_time_total == sum(l.subtotal for l in bom.lines if not l.recurring)
    assert bom.monthly_total == 2 * 400
    assert all(l.subtotal == l.quantity * l.unit_cost for l in bom.lines)


def test_bom_grows_with_redundancy():
    assert bill_of_materials(_plan()).one_time_total > bill_of_materials(
        _plan(redundancy=RedundancyLevel.none)
    ).one_time_total


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

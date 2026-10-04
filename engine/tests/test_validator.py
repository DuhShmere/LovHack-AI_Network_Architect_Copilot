"""Nyles: validator tests -- generated plans must pass, and every check must catch a broken plan."""

import itertools

import pytest

from shared.schema import NetworkSpec, RedundancyLevel, TopologyLink, TopologyNode, VLANAllocation
from engine.generator import generate_plan
from engine.validator import validate_plan

CHECK_NAMES = {
    "valid_ranges",
    "no_subnet_overlap",
    "valid_vlan_ids",
    "required_segments_present",
    "subnet_capacity",
    "topology_connected",
    "redundancy_present",
    "guest_isolation",
}
# Run on top of CHECK_NAMES once the design passes (engine/config_audit.py).
AUDIT_CHECK_NAMES = {"gateways_consistent", "no_ip_conflicts", "routing_complete"}


def _plan(**overrides):
    fields = dict(
        org_name="Test Office",
        user_count=50,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["staff", "iot"],
        redundancy=RedundancyLevel.dual_wan_plus_switch_redundancy,
    )
    fields.update(overrides)
    return generate_plan(NetworkSpec(**fields))


def _result(plan, name):
    report = validate_plan(plan)
    return next(c for c in report.checks if c.check_name == name), report


def _assert_fails(plan, name):
    report = validate_plan(plan)
    failed = {c.check_name for c in report.checks if not c.passed}
    assert not report.overall_pass
    assert name in failed, f"{name} should fail; failures were {failed}"


@pytest.mark.parametrize(
    "user_count, redundancy, guest, base",
    list(itertools.product(
        [1, 30, 62, 250, 600],
        list(RedundancyLevel),
        [False, True],
        [None, "172.16.0.0/12", "192.168.0.0/16"],
    )),
)
def test_every_generated_plan_passes(user_count, redundancy, guest, base):
    plan = _plan(
        user_count=user_count,
        redundancy=redundancy,
        needs_guest_wifi=guest,
        guest_wifi_isolated=guest,
        department_segments=["staff", "voip", "Front Desk"],
        preferred_base_cidr=base,
    )
    report = validate_plan(plan)
    failures = [f"{c.check_name}: {c.detail}" for c in report.checks if not c.passed]
    assert report.overall_pass, failures
    audits = AUDIT_CHECK_NAMES | ({"guest_isolation_enforced"} if guest else set())
    assert {c.check_name for c in report.checks} == CHECK_NAMES | audits


def test_details_are_specific():
    staff, _ = _result(_plan(), "subnet_capacity")
    assert "50 users" in staff.detail and "10.0.10.0/26" in staff.detail


def test_catches_overlapping_subnets():
    plan = _plan()
    plan.vlans[1].subnet_cidr = plan.vlans[0].subnet_cidr
    _assert_fails(plan, "no_subnet_overlap")


def test_catches_public_and_malformed_subnets():
    plan = _plan()
    plan.vlans[0].subnet_cidr = "8.8.8.0/24"
    plan.vlans[1].subnet_cidr = "10.0.20.5/24"
    check, _ = _result(plan, "valid_ranges")
    assert not check.passed
    assert "outside RFC1918" in check.detail and "not a valid network" in check.detail


def test_catches_duplicate_and_reserved_vlan_ids():
    plan = _plan()
    plan.vlans[1].vlan_id = plan.vlans[0].vlan_id
    plan.vlans.append(VLANAllocation(vlan_id=1, name="x", subnet_cidr="10.0.200.0/24", purpose="x"))
    check, _ = _result(plan, "valid_vlan_ids")
    assert not check.passed and "duplicate" in check.detail and "reserved" in check.detail


def test_catches_missing_segment():
    plan = _plan()
    plan.vlans = [v for v in plan.vlans if v.name != "iot"]
    _assert_fails(plan, "required_segments_present")


def test_catches_undersized_subnet():
    plan = _plan(user_count=30)
    staff = next(v for v in plan.vlans if v.name == "staff")
    staff.subnet_cidr = "10.0.10.0/27"  # 30 usable, but gateway needs one
    _assert_fails(plan, "subnet_capacity")


def test_catches_stranded_device():
    plan = _plan()
    plan.nodes.append(TopologyNode(node_id="orphan", node_type="access_switch", label="Orphan"))
    _assert_fails(plan, "topology_connected")


def test_catches_dangling_link():
    plan = _plan()
    plan.links.append(TopologyLink(source_id="core1", target_id="ghost", link_type="trunk"))
    check, _ = _result(plan, "topology_connected")
    assert not check.passed and "ghost" in check.detail


def test_catches_missing_second_wan():
    plan = _plan(redundancy=RedundancyLevel.dual_wan)
    plan.links = [l for l in plan.links if l.link_type != "redundant_wan"]
    plan.nodes = [n for n in plan.nodes if n.node_id != "isp-b"]
    check, _ = _result(plan, "redundancy_present")
    assert not check.passed and "dual WAN" in check.detail


def test_catches_single_homed_access_switch():
    plan = _plan()
    plan.links = [l for l in plan.links if not (l.source_id == "core2" and l.target_id == "access1")]
    check, _ = _result(plan, "redundancy_present")
    assert not check.passed and "access1" in check.detail


def test_catches_access_switch_daisy_chained_off_another():
    """Zero core uplinks but still online through another access switch: it
    reaches the WAN, so topology_connected passes, and it isn't dual-homed."""
    plan = _plan()
    core_uplinks = [{"core1", "access2"}, {"core2", "access2"}]
    plan.links = [l for l in plan.links if {l.source_id, l.target_id} not in core_uplinks]
    plan.links.append(TopologyLink(source_id="access1", target_id="access2", link_type="trunk"))
    check, report = _result(plan, "redundancy_present")
    assert not check.passed and "access2" in check.detail
    assert next(c for c in report.checks if c.check_name == "topology_connected").passed


def test_catches_firewall_bypass():
    plan = _plan()
    plan.links.append(TopologyLink(source_id="router1", target_id="core1", link_type="trunk"))
    check, _ = _result(plan, "guest_isolation")
    assert not check.passed and "without passing the firewall" in check.detail


def test_catches_missing_guest_vlan_when_isolation_required():
    plan = _plan()
    plan.vlans = [v for v in plan.vlans if v.name != "guest"]
    check, _ = _result(plan, "guest_isolation")
    assert not check.passed


def test_no_redundancy_requested_passes_without_second_wan():
    check, _ = _result(_plan(redundancy=RedundancyLevel.none), "redundancy_present")
    assert check.passed


def test_full_redundancy_requires_a_firewall_pair():
    plan = _plan()
    plan.nodes = [n for n in plan.nodes if n.node_id != "firewall2"]
    plan.links = [l for l in plan.links if "firewall2" not in (l.source_id, l.target_id)]
    check, _ = _result(plan, "redundancy_present")
    assert not check.passed and "firewall pair" in check.detail


def test_full_redundancy_requires_every_core_on_both_firewalls():
    plan = _plan()
    plan.links = [l for l in plan.links if {l.source_id, l.target_id} != {"firewall2", "core2"}]
    check, _ = _result(plan, "redundancy_present")
    assert not check.passed and "core2" in check.detail

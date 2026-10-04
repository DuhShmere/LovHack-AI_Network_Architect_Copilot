"""Nyles: standalone tests for the generator -- no API/server needed to run these."""

import ipaddress
import itertools

import pytest

from shared.schema import NetworkSpec, RedundancyLevel
from engine.generator import generate_plan, PlanGenerationError


def _spec(**overrides):
    fields = dict(org_name="Test Office", user_count=50)
    fields.update(overrides)
    return NetworkSpec(**fields)


def _vlans_by_name(plan):
    return {v.name: v for v in plan.vlans}


def _assert_no_overlap(plan):
    nets = [ipaddress.ip_network(v.subnet_cidr) for v in plan.vlans]
    for a, b in itertools.combinations(nets, 2):
        assert not a.overlaps(b), f"{a} overlaps {b}"


def _assert_links_reference_nodes(plan):
    ids = {n.node_id for n in plan.nodes}
    assert len(ids) == len(plan.nodes), "duplicate node_id"
    for link in plan.links:
        assert link.source_id in ids and link.target_id in ids


def test_generate_plan_basic_office():
    spec = _spec(
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["staff", "admin"],
    )
    plan = generate_plan(spec)
    assert plan.spec.org_name == "Test Office"

    vlans = _vlans_by_name(plan)
    assert set(vlans) == {"staff", "admin", "guest", "management"}
    assert vlans["staff"].vlan_id == 10
    assert vlans["staff"].subnet_cidr == "10.0.10.0/26"  # 50 users -> /26
    assert vlans["guest"].purpose == "Isolated guest wifi"
    assert vlans["admin"].vlan_id >= 100
    assert [v.vlan_id for v in plan.vlans] == sorted(v.vlan_id for v in plan.vlans)
    _assert_no_overlap(plan)
    _assert_links_reference_nodes(plan)


def test_matches_dashboard_fixture_addressing():
    spec = _spec(
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["staff", "guest", "iot"],
        redundancy="dual_wan",
        preferred_base_cidr="10.0.0.0/16",
    )
    vlans = {v.name: (v.vlan_id, v.subnet_cidr) for v in generate_plan(spec).vlans}
    assert vlans == {
        "staff": (10, "10.0.10.0/26"),
        "guest": (20, "10.0.20.0/26"),
        "iot": (30, "10.0.30.0/27"),
        "management": (99, "10.0.99.0/27"),  # fixture says /28; our floor is /27
    }


def test_segments_are_normalized_and_deduped():
    spec = _spec(
        needs_guest_wifi=True,
        department_segments=["Staff", "guest", " Front Desk ", "front desk", ""],
    )
    names = [v.name for v in generate_plan(spec).vlans]
    assert sorted(names) == sorted(["staff", "guest", "front-desk", "management"])


@pytest.mark.parametrize("alias", ["Guest WiFi", "guest wi-fi", "Visitors", "guests"])
def test_guest_aliases_map_to_the_one_guest_vlan(alias):
    # Was a second, un-isolated "guest-wifi" VLAN next to the isolated guest one.
    spec = _spec(needs_guest_wifi=True, guest_wifi_isolated=True, department_segments=[alias])
    names = [v.name for v in generate_plan(spec).vlans]
    assert sorted(names) == ["guest", "management", "staff"]


def test_catalog_aliases_get_catalog_vlans():
    spec = _spec(department_segments=["Voice", "Server Room", "mgmt", "Employees"])
    assert {v.name: v.vlan_id for v in generate_plan(spec).vlans} == {
        "staff": 10, "voip": 40, "servers": 50, "management": 99,
    }


def test_guest_isolation_implies_guest_wifi():
    plan = generate_plan(_spec(needs_guest_wifi=False, guest_wifi_isolated=True))
    assert plan.spec.needs_guest_wifi
    assert _vlans_by_name(plan)["guest"].purpose == "Isolated guest wifi"
    assert any("Guest Wi-Fi added" in a for a in plan.spec.assumptions)


def test_no_guest_vlan_unless_requested():
    assert "guest" not in _vlans_by_name(generate_plan(_spec()))


def test_no_redundancy_topology():
    plan = generate_plan(_spec(user_count=10))
    types = [n.node_type for n in plan.nodes]
    assert types.count("wan_uplink") == 1
    assert types.count("router") == 1
    assert types.count("core_switch") == 1
    assert types.count("access_switch") == 1
    assert "redundant_wan" not in {l.link_type for l in plan.links}
    _assert_links_reference_nodes(plan)


def test_dual_wan_topology():
    plan = generate_plan(_spec(redundancy=RedundancyLevel.dual_wan))
    types = [n.node_type for n in plan.nodes]
    assert types.count("wan_uplink") == 2
    assert types.count("router") == 2
    assert types.count("core_switch") == 1
    assert {"source_id": "isp-b", "target_id": "router2", "link_type": "redundant_wan"} in [
        l.model_dump() for l in plan.links
    ]
    _assert_links_reference_nodes(plan)


def test_switch_redundancy_dual_homes_access_switches():
    plan = generate_plan(
        _spec(user_count=100, redundancy=RedundancyLevel.dual_wan_plus_switch_redundancy)
    )
    cores = {n.node_id for n in plan.nodes if n.node_type == "core_switch"}
    assert cores == {"core1", "core2"}
    for access in (n.node_id for n in plan.nodes if n.node_type == "access_switch"):
        uplinks = {l.source_id for l in plan.links if l.target_id == access}
        assert uplinks == cores
    _assert_links_reference_nodes(plan)


def test_topology_scales_with_user_count():
    small = generate_plan(_spec(user_count=20))
    big = generate_plan(_spec(user_count=500))
    count = lambda plan, t: sum(n.node_type == t for n in plan.nodes)
    assert count(big, "access_switch") > count(small, "access_switch")
    assert count(big, "ap") > count(small, "ap")
    # every AP hangs off some access switch
    access_ids = {n.node_id for n in big.nodes if n.node_type == "access_switch"}
    for ap in (n.node_id for n in big.nodes if n.node_type == "ap"):
        assert any(l.target_id == ap and l.source_id in access_ids for l in big.links)


def test_large_org_falls_back_to_packed_layout():
    # 600 users needs a /22 per user-facing VLAN -- can't use the VLAN-per-octet layout.
    spec = _spec(user_count=600, needs_guest_wifi=True, department_segments=["voip"])
    plan = generate_plan(spec)
    base = ipaddress.ip_network("10.0.0.0/16")
    for v in plan.vlans:
        assert ipaddress.ip_network(v.subnet_cidr).subnet_of(base)
    assert _vlans_by_name(plan)["staff"].subnet_cidr.endswith("/22")
    _assert_no_overlap(plan)


def test_big_org_widens_the_default_base_and_says_so():
    # 9 VLANs sized for 5,000 users each need a /19: more than a /16 holds.
    depts = ["finance", "hr", "lab", "sales", "support", "ops", "legal"]
    plan = generate_plan(_spec(user_count=5000, needs_guest_wifi=True, department_segments=depts))
    base = ipaddress.ip_network("10.0.0.0/15")
    for v in plan.vlans:
        assert ipaddress.ip_network(v.subnet_cidr).subnet_of(base)
    _assert_no_overlap(plan)
    assert any("widened to 10.0.0.0/15" in a for a in plan.spec.assumptions)


def test_regenerating_a_plans_own_spec_doesnt_repeat_its_notes():
    # /refine sends the generated spec back through the generator.
    spec = _spec(user_count=5000, needs_guest_wifi=False, guest_wifi_isolated=True,
                 department_segments=["finance", "hr", "lab", "sales", "support", "ops", "legal"])
    first = generate_plan(spec)
    again = generate_plan(first.spec)
    assert again.spec.assumptions == first.spec.assumptions
    assert len(first.spec.assumptions) == 2


def test_users_past_a_single_site_are_rejected_clearly():
    with pytest.raises(PlanGenerationError, match="up to 5,000 users"):
        generate_plan(_spec(user_count=60_000))


def test_default_base_is_not_widened_when_it_fits():
    plan = generate_plan(_spec(user_count=600))
    assert not any("widened" in a for a in plan.spec.assumptions)


def test_too_small_preferred_base_says_how_big_it_must_be():
    with pytest.raises(PlanGenerationError, match=r"192.168.1.0/24 is too small.*at least a /22"):
        generate_plan(_spec(user_count=500, preferred_base_cidr="192.168.1.0/24"))


@pytest.mark.parametrize("users", [1, 24, 44, 45, 48, 90, 96, 97, 250, 1000])
@pytest.mark.parametrize("redundancy", list(RedundancyLevel))
def test_access_switches_have_a_port_for_every_user(users, redundancy):
    # Each switch's core uplinks and APs use ports too: the vet hospital
    # (90 users, dual core) used to get 2 switches with only 88 user ports.
    plan = generate_plan(_spec(user_count=users, redundancy=redundancy))
    access = [n.node_id for n in plan.nodes if n.node_type == "access_switch"]
    used = sum(1 for l in plan.links for end in (l.source_id, l.target_id) if end in access)
    assert len(access) * 48 - used >= users
    # ...without buying a switch more than needed.
    assert (len(access) - 1) * 48 - used < users or len(access) == 1


def test_small_preferred_base_is_packed():
    spec = _spec(user_count=20, department_segments=["iot"], preferred_base_cidr="192.168.1.0/24")
    plan = generate_plan(spec)
    base = ipaddress.ip_network("192.168.1.0/24")
    for v in plan.vlans:
        assert ipaddress.ip_network(v.subnet_cidr).subnet_of(base)
    _assert_no_overlap(plan)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(preferred_base_cidr="not-a-cidr"),
        dict(preferred_base_cidr="fd00::/48"),
        dict(user_count=500, preferred_base_cidr="192.168.1.0/24"),  # doesn't fit
        dict(user_count=0),
    ],
)
def test_impossible_specs_raise(overrides):
    with pytest.raises(PlanGenerationError):
        generate_plan(_spec(**overrides))

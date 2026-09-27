"""Nyles: standalone tests for the generator -- no API/server needed to run these."""

import ipaddress

import pytest

from shared.schema import NetworkSpec
from engine.generator import generate_plan


def test_generate_plan_basic_office():
    spec = NetworkSpec(
        org_name="Test Office",
        user_count=50,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["staff", "admin"],
    )
    plan = generate_plan(spec)
    assert plan.spec.org_name == "Test Office"
    assert [vlan.name for vlan in plan.vlans] == ["staff", "admin", "guest"]
    assert len({vlan.vlan_id for vlan in plan.vlans}) == len(plan.vlans)
    subnets = [ipaddress.ip_network(vlan.subnet_cidr) for vlan in plan.vlans]
    assert all(
        not left.overlaps(right)
        for index, left in enumerate(subnets)
        for right in subnets[index + 1:]
    )
    assert any(node.node_type == "ap" for node in plan.nodes)


def test_generate_plan_adds_redundant_wan_and_core():
    spec = NetworkSpec(
        org_name="Resilient Office",
        user_count=120,
        redundancy="dual_wan_plus_switch_redundancy",
    )

    plan = generate_plan(spec)

    assert sum(node.node_type == "router" for node in plan.nodes) == 2
    assert sum(node.node_type == "core_switch" for node in plan.nodes) == 2
    assert sum(link.link_type == "redundant_wan" for link in plan.links) == 1
    assert sum(node.node_type == "access_switch" for node in plan.nodes) == 3


def test_generate_plan_rejects_address_pool_that_is_too_small():
    spec = NetworkSpec(
        org_name="Small Pool",
        user_count=50,
        needs_guest_wifi=True,
        department_segments=["staff", "admin"],
        preferred_base_cidr="192.168.1.0/25",
    )

    with pytest.raises(ValueError, match="cannot fit"):
        generate_plan(spec)

"""Standalone tests for illustrative Cisco-style device configurations."""

from shared.schema import NetworkSpec
from engine.config_gen import generate_configs
from engine.generator import generate_plan
from engine.validator import validate_plan


def test_generates_one_config_per_topology_node():
    plan = generate_plan(NetworkSpec(
        org_name="Test Office",
        user_count=30,
        needs_guest_wifi=True,
        department_segments=["staff"],
    ))

    configs = generate_configs(plan)

    assert [config.node_id for config in configs] == [node.node_id for node in plan.nodes]
    assert all(config.config_text.startswith("hostname ") for config in configs)
    assert all("Illustrative only" in config.config_text for config in configs)


def test_switch_configs_include_vlans_trunks_and_guest_ap_port():
    plan = generate_plan(NetworkSpec(
        org_name="Guest Office",
        user_count=30,
        needs_guest_wifi=True,
        department_segments=["staff"],
    ))

    configs = {config.node_id: config.config_text for config in generate_configs(plan)}

    assert "vlan 10\n name staff" in configs["access-switch-1"]
    assert "vlan 20\n name guest" in configs["access-switch-1"]
    assert "switchport mode trunk" in configs["access-switch-1"]
    assert "switchport access vlan 20" in configs["access-switch-1"]


def test_does_not_invent_ip_addresses_or_routing():
    plan = generate_plan(NetworkSpec(org_name="Small Office", user_count=12))

    configs = generate_configs(plan)

    for config in configs:
        assert "ip address " not in config.config_text
        assert "ip route " not in config.config_text


def test_redundant_guest_plan_flows_through_validation_and_config_generation():
    plan = generate_plan(NetworkSpec(
        org_name="Resilient Guest Office",
        user_count=120,
        needs_guest_wifi=True,
        redundancy="dual_wan_plus_switch_redundancy",
        department_segments=["staff", "servers"],
    ))

    validation = validate_plan(plan)
    configs = generate_configs(plan) if validation.overall_pass else []

    assert validation.overall_pass
    assert len(configs) == len(plan.nodes)
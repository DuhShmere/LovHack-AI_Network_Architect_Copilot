"""Standalone tests for deterministic network-plan validation."""

from shared.schema import NetworkSpec, TopologyLink, TopologyNode
from engine.generator import generate_plan
from engine.validator import validate_plan


def test_generated_plan_passes_validation():
    plan = generate_plan(NetworkSpec(
        org_name="Test Office",
        user_count=50,
        department_segments=["staff", "servers"],
    ))

    report = validate_plan(plan)

    assert report.overall_pass
    assert all(check.passed for check in report.checks)


def test_validator_finds_overlapping_subnets():
    plan = generate_plan(NetworkSpec(
        org_name="Overlap Office",
        user_count=25,
        department_segments=["staff", "servers"],
    ))
    duplicated_vlan = plan.vlans[1].model_copy(
        update={"subnet_cidr": plan.vlans[0].subnet_cidr}
    )
    invalid_plan = plan.model_copy(update={
        "vlans": [plan.vlans[0], duplicated_vlan],
    })

    report = validate_plan(invalid_plan)

    overlap_check = next(check for check in report.checks if check.check_name == "no_subnet_overlap")
    assert not overlap_check.passed
    assert not report.overall_pass


def test_validator_counts_capacity_across_segments():
    plan = generate_plan(NetworkSpec(
        org_name="Segmented Office",
        user_count=50,
        needs_guest_wifi=True,
        department_segments=["staff", "iot", "management"],
    ))
    subnets = [
        "10.0.10.0/26",
        "10.0.20.0/26",
        "10.0.30.0/27",
        "10.0.99.0/28",
    ]
    plan = plan.model_copy(update={
        "vlans": [
            vlan.model_copy(update={"subnet_cidr": subnet})
            for vlan, subnet in zip(plan.vlans, subnets)
        ],
    })

    report = validate_plan(plan)

    capacity_check = next(
        check for check in report.checks if check.check_name == "subnet_capacity"
    )
    assert capacity_check.passed
    assert "Aggregate VLAN capacity" in capacity_check.detail


def test_validator_rejects_insufficient_aggregate_subnet_capacity():
    plan = generate_plan(NetworkSpec(
        org_name="Small Pool Office",
        user_count=50,
        needs_guest_wifi=True,
        department_segments=["staff", "iot", "management"],
    ))
    subnets = [
        "10.0.0.0/29",
        "10.0.0.8/29",
        "10.0.0.16/29",
        "10.0.0.24/29",
    ]
    plan = plan.model_copy(update={
        "vlans": [
            vlan.model_copy(update={"subnet_cidr": subnet})
            for vlan, subnet in zip(plan.vlans, subnets)
        ],
    })

    report = validate_plan(plan)

    capacity_check = next(
        check for check in report.checks if check.check_name == "subnet_capacity"
    )
    assert not capacity_check.passed
    assert "54 are required" in capacity_check.detail


def test_validator_finds_missing_redundant_wan_path():
    plan = generate_plan(NetworkSpec(
        org_name="Resilient Office",
        user_count=30,
        redundancy="dual_wan",
    ))
    invalid_plan = plan.model_copy(update={
        "links": [link for link in plan.links if link.link_type != "redundant_wan"],
    })

    report = validate_plan(invalid_plan)

    redundancy_check = next(
        check for check in report.checks if check.check_name == "redundancy_present"
    )
    assert not redundancy_check.passed


def test_validator_accepts_wan_uplinks_connected_to_distinct_routers():
    plan = generate_plan(NetworkSpec(
        org_name="Two Uplink Office",
        user_count=30,
        redundancy="dual_wan",
    ))
    uplink_links = [
        TopologyLink(source_id="isp-a", target_id="router-primary", link_type="wan"),
        TopologyLink(
            source_id="isp-b",
            target_id="router-secondary",
            link_type="redundant_wan",
        ),
    ]
    plan = plan.model_copy(update={
        "nodes": plan.nodes + [
            TopologyNode(node_id="isp-a", node_type="wan_uplink", label="ISP A"),
            TopologyNode(node_id="isp-b", node_type="wan_uplink", label="ISP B"),
        ],
        "links": [
            link for link in plan.links
            if link.link_type not in {"wan", "redundant_wan"}
        ] + uplink_links,
    })

    report = validate_plan(plan)

    redundancy_check = next(
        check for check in report.checks if check.check_name == "redundancy_present"
    )
    assert redundancy_check.passed


def test_validator_does_not_claim_guest_isolation_without_policy_data():
    plan = generate_plan(NetworkSpec(
        org_name="Guest Office",
        user_count=30,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
    ))

    report = validate_plan(plan)

    guest_check = next(check for check in report.checks if check.check_name == "guest_isolation")
    assert not guest_check.passed
    assert "cannot be verified" in guest_check.detail
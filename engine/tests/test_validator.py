"""Standalone tests for deterministic network-plan validation."""

from shared.schema import NetworkSpec
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
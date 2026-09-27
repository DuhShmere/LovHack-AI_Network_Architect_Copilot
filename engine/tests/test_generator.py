"""Nyles: standalone tests for the generator -- no API/server needed to run these."""

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
    # TODO: assert on vlans/nodes/links once generate_plan is implemented

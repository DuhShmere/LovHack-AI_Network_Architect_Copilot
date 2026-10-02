"""Nyles: demo sabotage tests -- every sabotage must be caught by exactly the check it targets."""

import itertools

import pytest

from shared.schema import NetworkSpec, RedundancyLevel
from engine.generator import generate_plan
from engine.validator import validate_plan
from engine.demo import SabotageError, break_design, list_sabotages, sabotage_plan, _CONFIG_SABOTAGES, _SABOTAGES


def _plan(**overrides):
    fields = dict(
        org_name="Acme Dental",
        user_count=50,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["staff", "iot"],
        redundancy=RedundancyLevel.dual_wan,
    )
    fields.update(overrides)
    return generate_plan(NetworkSpec(**fields))


SPECS = [
    dict(user_count=u, redundancy=r, needs_guest_wifi=g, guest_wifi_isolated=g, department_segments=d)
    for u, r, g, d in itertools.product(
        [1, 50, 600],
        list(RedundancyLevel),
        [False, True],
        [[], ["staff", "voip"]],
    )
]


@pytest.mark.parametrize("overrides", SPECS)
def test_every_applicable_sabotage_fails_its_target_check(overrides):
    plan = _plan(**overrides)
    assert validate_plan(plan).overall_pass
    for info in [s for s in list_sabotages(plan) if s.kind == "design"]:
        broken, what = sabotage_plan(plan, info.key)
        report = validate_plan(broken)
        failed = {c.check_name for c in report.checks if not c.passed}
        assert not report.overall_pass, info.key
        assert info.target_check in failed, (info.key, failed)
        assert what.endswith(".")


def test_full_featured_plan_offers_every_sabotage():
    keys = {s.key for s in list_sabotages(_plan())}
    assert keys == set(_SABOTAGES) | set(_CONFIG_SABOTAGES)


def test_sabotages_that_need_features_are_hidden_without_them():
    plan = _plan(redundancy=RedundancyLevel.none, guest_wifi_isolated=False)
    keys = {s.key for s in list_sabotages(plan)}
    assert "missing_backup_wan" not in keys
    assert "firewall_bypass" not in keys
    with pytest.raises(SabotageError):
        sabotage_plan(plan, "missing_backup_wan")


def test_original_plan_is_untouched():
    plan = _plan()
    before = plan.model_dump()
    for key in _SABOTAGES:
        sabotage_plan(plan, key)
    assert plan.model_dump() == before
    assert validate_plan(plan).overall_pass


def test_unknown_key_raises():
    with pytest.raises(SabotageError, match="Options"):
        sabotage_plan(_plan(), "nope")


def test_undersized_is_one_size_too_small_for_typical_office():
    broken, what = sabotage_plan(_plan(), "undersized_subnet")
    staff = next(v for v in broken.vlans if v.name == "staff")
    assert staff.subnet_cidr == "10.0.10.0/27"
    assert "for 50 users" in what


def test_each_sabotage_fails_only_its_target_on_typical_plan():
    """Keeps the demo story clean: break one thing, exactly one check goes red."""
    plan = _plan()
    for info in [s for s in list_sabotages(plan) if s.kind == "design"]:
        broken, _ = sabotage_plan(plan, info.key)
        failed = {c.check_name for c in validate_plan(broken).checks if not c.passed}
        assert failed == {info.target_check}, (info.key, failed)


@pytest.mark.parametrize("overrides", SPECS)
def test_every_sabotage_fails_only_its_target_via_break_design(overrides):
    plan = _plan(**overrides)
    for info in list_sabotages(plan):
        broken, what, report = break_design(plan, info.key)
        failed = {c.check_name for c in report.checks if not c.passed}
        assert failed == {info.target_check}, (info.key, failed)
        assert what.endswith(".")


def test_config_sabotages_leave_the_plan_alone():
    plan = _plan()
    before = plan.model_dump()
    for key in _CONFIG_SABOTAGES:
        broken, _, report = break_design(plan, key)
        assert broken.model_dump() == before
        design = [c for c in report.checks if c.check_name in {
            "valid_ranges", "no_subnet_overlap", "valid_vlan_ids", "required_segments_present",
            "subnet_capacity", "topology_connected", "redundancy_present", "guest_isolation",
        }]
        assert len(design) == 8 and all(c.passed for c in design), key


def test_config_sabotages_are_labelled_as_config():
    kinds = {s.key: s.kind for s in list_sabotages(_plan())}
    assert all(kinds[k] == "config" for k in _CONFIG_SABOTAGES)
    assert all(kinds[k] == "design" for k in _SABOTAGES)


def test_inapplicable_config_sabotage_raises():
    with pytest.raises(SabotageError):
        break_design(_plan(guest_wifi_isolated=False), "strip_guest_acl")

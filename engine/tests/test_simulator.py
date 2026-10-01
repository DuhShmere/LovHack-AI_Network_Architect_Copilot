"""Nyles: simulator tests -- traffic follows the configs, and failures play out the way the design claims."""

import itertools

import pytest

from shared.schema import NetworkSpec, RedundancyLevel
from engine.generator import generate_plan
from engine.simulator import INTERNET, SimulationError, resilience, simulate
import engine.simulator as simulator


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


def _flow(report, src, dst):
    return next(f for f in report.flows if f.source == src and f.destination == dst)


@pytest.mark.parametrize("users, redundancy, guest", list(itertools.product(
    [1, 120, 600], list(RedundancyLevel), [False, True],
)))
def test_healthy_network_reaches_the_internet_from_every_vlan(users, redundancy, guest):
    plan = _plan(user_count=users, redundancy=redundancy, needs_guest_wifi=guest, guest_wifi_isolated=guest)
    report = simulate(plan)
    internet = [f for f in report.flows if f.destination == INTERNET]
    assert len(internet) == len(plan.vlans)
    assert all(f.allowed for f in internet), [f.reason for f in internet if not f.allowed]
    assert all(f.path[-2].startswith("isp-") and f.path[-1] == INTERNET for f in internet)
    assert report.stranded == []


def test_guest_is_blocked_from_every_internal_vlan_but_others_are_routed():
    report = simulate(_plan())
    for f in report.flows:
        if f.destination == INTERNET:
            continue
        if f.source == "guest":
            assert not f.allowed and "GUEST-ISOLATION" in f.reason, f
        else:
            assert f.allowed, f
            assert f.path[-1] == f.destination


def test_guest_without_isolation_is_routed():
    report = simulate(_plan(guest_wifi_isolated=False))
    assert _flow(report, "guest", "staff").allowed


def test_flow_paths_walk_the_topology():
    plan = _plan()
    linked = {frozenset((l.source_id, l.target_id)) for l in plan.links}
    for f in simulate(plan).flows:
        hops = [h for h in f.path if h not in (INTERNET, f.destination)]
        for a, b in zip(hops, hops[1:]):
            assert frozenset((a, b)) in linked, (f.path, a, b)


def test_hsrp_fails_over_to_the_standby_core():
    report = simulate(_plan(), ["core1"])
    f = _flow(report, "staff", INTERNET)
    assert f.allowed and "core2" in f.path and "core1" not in f.path


def test_isp_failure_moves_traffic_to_the_backup_circuit():
    # router1 stays up; only its tracked default route notices ISP A is gone.
    f = _flow(simulate(_plan(), ["isp-a"]), "staff", INTERNET)
    assert f.allowed and f.path[-2:] == ["isp-b", INTERNET], f


def test_without_ip_sla_tracking_an_isp_failure_blackholes_traffic(monkeypatch):
    real = simulator.generate_configs

    def untracked(plan):
        configs = real(plan)
        for c in configs:
            c.config_text = c.config_text.replace(" track 1\n", "\n")
        return configs

    monkeypatch.setattr(simulator, "generate_configs", untracked)
    f = _flow(simulate(_plan(), ["isp-a"]), "staff", INTERNET)
    assert not f.allowed and "router1" in f.reason and "ISP circuit is down" in f.reason


def test_firewall_is_the_only_single_point_of_failure_with_full_redundancy():
    report = resilience(_plan())
    assert report.single_points_of_failure == ["firewall1"]
    assert "firewall1" in report.summary


def test_without_redundancy_the_whole_edge_is_a_single_point_of_failure():
    report = resilience(_plan(redundancy=RedundancyLevel.none))
    assert set(report.single_points_of_failure) == {"isp-a", "router1", "firewall1", "core1"}


def test_access_switch_failure_strands_only_its_own_aps():
    plan = _plan()
    report = simulate(plan, ["access1"])
    on_access1 = {l.target_id for l in plan.links if l.source_id == "access1" and l.target_id.startswith("ap")}
    assert on_access1 and set(report.stranded) == on_access1
    assert _flow(report, "staff", INTERNET).allowed  # other access switches carry the VLAN


def test_losing_the_only_access_switch_cuts_everyone_off():
    report = simulate(_plan(user_count=10), ["access1"])
    f = _flow(report, "staff", INTERNET)
    assert not f.allowed and "access switch" in f.reason


def test_unknown_device_is_rejected():
    with pytest.raises(SimulationError, match="nope"):
        simulate(_plan(), ["nope"])

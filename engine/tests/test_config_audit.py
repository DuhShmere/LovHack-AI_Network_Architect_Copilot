"""Nyles: config audit tests -- generated configs must pass, and every audit check must catch a tampered config."""

import ipaddress
import itertools
import re

import pytest

from shared.schema import DeviceConfig, NetworkSpec, RedundancyLevel
from engine.generator import generate_plan
from engine.config_gen import generate_configs
from engine.config_audit import _AclEntry, acl_verdict, audit_configs
from engine.validator import validate_plan

AUDIT_CHECKS = {"guest_isolation_enforced", "gateways_consistent", "no_ip_conflicts", "routing_complete"}


def _build(**overrides):
    fields = dict(
        org_name="Acme Dental",
        user_count=50,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["staff", "iot", "voip"],
        redundancy=RedundancyLevel.dual_wan_plus_switch_redundancy,
    )
    fields.update(overrides)
    plan = generate_plan(NetworkSpec(**fields))
    return plan, {c.node_id: c.config_text for c in generate_configs(plan)}


def _audit(plan, texts):
    checks = audit_configs(plan, [DeviceConfig(node_id=k, config_text=v) for k, v in texts.items()])
    return {c.check_name: c for c in checks}


def _assert_only_fails(plan, texts, name, detail_part=""):
    results = _audit(plan, texts)
    failed = {n for n, c in results.items() if not c.passed}
    assert failed == {name}, failed
    assert detail_part in results[name].detail, results[name].detail


def _replace(text, old, new, count=1):
    assert old in text, old
    return text.replace(old, new, count)


# ---------------------------------------------------------------------------
# Generated configs pass
# ---------------------------------------------------------------------------

SPECS = [
    dict(user_count=u, redundancy=r, needs_guest_wifi=g, guest_wifi_isolated=g, department_segments=d)
    for u, r, g, d in itertools.product(
        [1, 50, 264, 600],
        list(RedundancyLevel),
        [False, True],
        [[], ["staff", "voip", "iot", "finance", "servers"]],
    )
] + [dict(preferred_base_cidr="172.16.0.0/20"), dict(preferred_base_cidr="192.168.0.0/22", user_count=20)]


@pytest.mark.parametrize("overrides", SPECS)
def test_generated_configs_pass_every_audit_check(overrides):
    plan, texts = _build(**overrides)
    results = _audit(plan, texts)
    expected = AUDIT_CHECKS if plan.spec.guest_wifi_isolated else AUDIT_CHECKS - {"guest_isolation_enforced"}
    assert set(results) == expected
    assert all(c.passed for c in results.values()), [c.detail for c in results.values() if not c.passed]


def test_validator_appends_audit_to_a_passing_design():
    plan, _ = _build()
    report = validate_plan(plan)
    assert report.overall_pass
    assert AUDIT_CHECKS <= {c.check_name for c in report.checks}


def test_validator_skips_audit_for_a_broken_design():
    plan, _ = _build()
    plan.vlans[1].subnet_cidr = plan.vlans[0].subnet_cidr  # overlap
    names = {c.check_name for c in validate_plan(plan).checks}
    assert not names & AUDIT_CHECKS


# ---------------------------------------------------------------------------
# Guest isolation
# ---------------------------------------------------------------------------

def test_missing_access_group_is_caught():
    plan, texts = _build()
    texts["core2"] = _replace(texts["core2"], " ip access-group GUEST-ISOLATION in\n", "")
    _assert_only_fails(plan, texts, "guest_isolation_enforced", "core2 Vlan20 routes guest traffic with no inbound ACL")


def test_undefined_acl_is_caught():
    plan, texts = _build()
    texts["core1"] = _replace(texts["core1"], "ip access-list extended GUEST-ISOLATION", "ip access-list extended RENAMED")
    _assert_only_fails(plan, texts, "guest_isolation_enforced", "isn't defined")


def test_early_permit_leaks_every_subnet():
    plan, texts = _build()
    texts["core1"] = _replace(
        texts["core1"], "ip access-list extended GUEST-ISOLATION\n",
        "ip access-list extended GUEST-ISOLATION\n permit ip any any\n",
    )
    _assert_only_fails(plan, texts, "guest_isolation_enforced", "lets guests reach")


def test_one_missing_deny_names_the_leaked_subnet():
    plan, texts = _build()
    iot = ipaddress.ip_network(next(v.subnet_cidr for v in plan.vlans if v.name == "iot"))
    line = next(l for l in texts["core1"].splitlines() if l.startswith(" deny ip") and l.endswith(f"{iot.network_address} {iot.hostmask}"))
    texts["core1"] = _replace(texts["core1"], line + "\n", "")
    _assert_only_fails(plan, texts, "guest_isolation_enforced", str(iot))


def test_tcp_only_deny_is_not_isolation():
    plan, texts = _build()
    texts["core1"] = re.sub(r"^ deny ip ", " deny tcp ", texts["core1"], flags=re.M)
    _assert_only_fails(plan, texts, "guest_isolation_enforced", "lets guests reach")


def test_blocking_the_internet_is_caught():
    plan, texts = _build()
    texts["core1"] = _replace(texts["core1"], " permit ip any any\n", " deny ip any any\n")
    _assert_only_fails(plan, texts, "guest_isolation_enforced", "blocks guests from the internet")


# ---------------------------------------------------------------------------
# Gateways, addressing, routing
# ---------------------------------------------------------------------------

def test_hsrp_vip_disagreement_is_caught():
    plan, texts = _build()
    vip = re.search(r"interface Vlan10\n.*?standby 1 ip (\S+)", texts["core2"], re.S).group(1)
    wrong = str(ipaddress.ip_address(vip) + 8)  # still inside the DHCP-excluded range
    texts["core2"] = _replace(texts["core2"], f"standby 1 ip {vip}\n", f"standby 1 ip {wrong}\n")
    _assert_only_fails(plan, texts, "gateways_consistent", "disagree on the HSRP virtual IP")


def test_wrong_dhcp_default_router_is_caught():
    plan, texts = _build(redundancy=RedundancyLevel.none)
    gw = re.search(r"ip dhcp pool STAFF\n.*?default-router (\S+)", texts["core1"], re.S).group(1)
    texts["core1"] = _replace(texts["core1"], f" default-router {gw}\n", f" default-router {ipaddress.ip_address(gw) + 5}\n")
    _assert_only_fails(plan, texts, "gateways_consistent", "DHCP pool STAFF")


def test_duplicate_static_address_is_caught():
    plan, texts = _build()
    a1 = re.search(r"interface Vlan99\n.*? ip address (\S+)", texts["access1"], re.S).group(1)
    a2 = re.search(r"interface Vlan99\n.*? ip address (\S+)", texts["access2"], re.S).group(1)
    texts["access2"] = _replace(texts["access2"], f" ip address {a2} ", f" ip address {a1} ")
    _assert_only_fails(plan, texts, "no_ip_conflicts", f"{a1} is configured on access1 Vlan99 and access2 Vlan99")


def test_dhcp_pool_overlapping_static_address_is_caught():
    plan, texts = _build()  # dual core: SVIs at .2 and .3 sit inside the pool
    texts["core1"] = re.sub(r"^ip dhcp excluded-address (\S+) \S+$", r"ip dhcp excluded-address \1", texts["core1"], flags=re.M)
    _assert_only_fails(plan, texts, "no_ip_conflicts", "can lease")


def test_missing_firewall_return_route_is_caught():
    # Without it the firewall falls back to its default route, back out to the router.
    plan, texts = _build()
    staff = ipaddress.ip_network(next(v.subnet_cidr for v in plan.vlans if v.name == "staff"))
    texts["firewall1"] = re.sub(rf"^ip route {staff.network_address} {staff.netmask} \S+\n", "", texts["firewall1"], flags=re.M)
    _assert_only_fails(plan, texts, "routing_complete", "routing loop via router1 -> firewall1 -> router1")


def test_broken_failover_default_route_is_caught():
    plan, texts = _build()
    texts["firewall1"] = re.sub(r"^(ip route 0\.0\.0\.0 0\.0\.0\.0 )(\S+)( 10)$", r"\g<1>203.0.113.1\3", texts["firewall1"], flags=re.M)
    _assert_only_fails(plan, texts, "routing_complete", "isn't on any of its interfaces")


def test_routing_loop_is_caught():
    plan, texts = _build(redundancy=RedundancyLevel.none)
    # Firewall now sends staff back to the router, which sends it to the firewall.
    staff = ipaddress.ip_network(next(v.subnet_cidr for v in plan.vlans if v.name == "staff"))
    router_side = re.search(r"^ip route 0\.0\.0\.0 0\.0\.0\.0 (\S+)$", texts["firewall1"], re.M).group(1)
    texts["firewall1"] = re.sub(
        rf"^ip route {staff.network_address} {staff.netmask} \S+$",
        f"ip route {staff.network_address} {staff.netmask} {router_side}",
        texts["firewall1"], flags=re.M,
    )
    _assert_only_fails(plan, texts, "routing_complete", "routing loop")


def test_missing_nat_entry_is_caught():
    plan, texts = _build()
    first = re.search(r"ip access-list standard NAT-INSIDE\n( permit .*\n)", texts["router1"]).group(1)
    texts["router1"] = _replace(texts["router1"], first, "")
    _assert_only_fails(plan, texts, "routing_complete", "router1 doesn't NAT")


def test_garbled_config_fails_instead_of_raising():
    plan, texts = _build()
    texts["core1"] = _replace(texts["core1"], " network ", " network not-an-ip ")
    results = _audit(plan, texts)
    assert not all(c.passed for c in results.values())


# ---------------------------------------------------------------------------
# ACL evaluation
# ---------------------------------------------------------------------------

N = ipaddress.ip_network
GUEST, STAFF = N("10.0.20.0/24"), N("10.0.10.0/24")


def _ace(action, src, dst, all_traffic=True):
    return _AclEntry(action, N(src), N(dst), all_traffic)


@pytest.mark.parametrize("entries, expected", [
    ([], "deny"),  # implicit deny
    ([_ace("permit", "0.0.0.0/0", "0.0.0.0/0")], "permit"),
    ([_ace("deny", "10.0.20.0/24", "10.0.10.0/24"), _ace("permit", "0.0.0.0/0", "0.0.0.0/0")], "deny"),
    # Covers only half of staff, then permit: the other half leaks.
    ([_ace("deny", "10.0.20.0/24", "10.0.10.0/25"), _ace("permit", "0.0.0.0/0", "0.0.0.0/0")], "mixed"),
    # Two halves together do cover it, but we refuse to credit that: conservative "mixed".
    ([_ace("deny", "10.0.20.0/24", "10.0.10.0/25"), _ace("deny", "10.0.20.0/24", "10.0.10.128/25"),
      _ace("permit", "0.0.0.0/0", "0.0.0.0/0")], "mixed"),
    # Protocol-limited deny doesn't block everything.
    ([_ace("deny", "10.0.20.0/24", "10.0.10.0/24", all_traffic=False), _ace("permit", "0.0.0.0/0", "0.0.0.0/0")], "mixed"),
    # A protocol-limited permit before the implicit deny is a leak.
    ([_ace("permit", "0.0.0.0/0", "0.0.0.0/0", all_traffic=False)], "mixed"),
    # Non-overlapping entries are skipped.
    ([_ace("permit", "192.168.0.0/16", "0.0.0.0/0")], "deny"),
])
def test_acl_verdict(entries, expected):
    assert acl_verdict(entries, GUEST, STAFF) == expected

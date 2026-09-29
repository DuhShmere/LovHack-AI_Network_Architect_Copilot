"""Nyles: config generator tests -- configs must agree with the plan and with each other."""

import ipaddress
import itertools
import re

import pytest

from shared.schema import NetworkSpec, RedundancyLevel
from engine.generator import generate_plan
from engine.config_gen import generate_configs


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


def _ip_addresses(text):
    return re.findall(r"^ ip address (\S+) (\S+)$", text, re.M)


def test_one_config_per_managed_device():
    plan, configs = _build()
    managed = {n.node_id for n in plan.nodes if n.node_type != "wan_uplink"}
    assert set(configs) == managed
    for node_id, text in configs.items():
        assert text.startswith(f"hostname {node_id.upper()}\n")
        assert text.rstrip().endswith("end")


def test_core_has_gateway_for_every_vlan():
    plan, configs = _build(redundancy=RedundancyLevel.none)
    core = configs["core1"]
    for v in plan.vlans:
        net = ipaddress.ip_network(v.subnet_cidr)
        assert f"interface Vlan{v.vlan_id}\n" in core
        assert f" ip address {next(net.hosts())} {net.netmask}" in core
    assert "standby" not in core


def test_dual_core_uses_hsrp_with_shared_virtual_gateway():
    plan, configs = _build()
    for v in plan.vlans:
        vip = next(ipaddress.ip_network(v.subnet_cidr).hosts())
        for core in ("core1", "core2"):
            assert f"standby 1 ip {vip}" in configs[core]
    assert "priority 110" in configs["core1"] and "priority 100" in configs["core2"]
    # DHCP only on the primary so leases don't collide.
    assert "ip dhcp pool" in configs["core1"] and "ip dhcp pool" not in configs["core2"]


def test_no_duplicate_ip_addresses_across_devices():
    _, configs = _build()
    addrs = [ip for text in configs.values() for ip, _ in _ip_addresses(text)]
    assert len(addrs) == len(set(addrs)), sorted(a for a in addrs if addrs.count(a) > 1)


def test_static_route_next_hops_are_reachable_neighbors():
    """Every next hop must sit on a subnet the same device has an interface in."""
    _, configs = _build()
    for node_id, text in configs.items():
        connected = [
            ipaddress.ip_interface(f"{ip}/{mask}").network for ip, mask in _ip_addresses(text)
        ]
        for hop in re.findall(r"^ip route \S+ \S+ (\d+\.\d+\.\d+\.\d+)", text, re.M):
            assert any(ipaddress.ip_address(hop) in net for net in connected), (node_id, hop)


def test_firewall_floating_default_only_with_dual_wan():
    _, dual = _build(redundancy=RedundancyLevel.dual_wan)
    _, single = _build(redundancy=RedundancyLevel.none)
    defaults = lambda text: re.findall(r"^ip route 0\.0\.0\.0 0\.0\.0\.0 .*$", text, re.M)
    assert len(defaults(dual["firewall1"])) == 2
    assert defaults(dual["firewall1"])[1].endswith(" 10")
    assert len(defaults(single["firewall1"])) == 1


def test_guest_isolation_acl_blocks_every_internal_subnet():
    plan, configs = _build()
    core = configs["core1"]
    guest = next(v for v in plan.vlans if v.name == "guest")
    assert "ip access-group GUEST-ISOLATION in" in core.split(f"interface Vlan{guest.vlan_id}\n")[1].split("!")[0]
    for v in plan.vlans:
        if v.vlan_id != guest.vlan_id:
            net = ipaddress.ip_network(v.subnet_cidr)
            assert f"{net.network_address} {net.hostmask}\n" in core.split("GUEST-ISOLATION\n")[1]


def test_no_guest_acl_when_isolation_not_requested():
    _, configs = _build(guest_wifi_isolated=False)
    assert "GUEST-ISOLATION" not in configs["core1"]


def test_access_switch_ports_and_voice_vlan():
    plan, configs = _build()
    access = configs["access1"]
    assert "switchport access vlan 10" in access
    assert "switchport voice vlan 40" in access
    _, no_voip = _build(department_segments=["staff"])
    assert "voice vlan" not in no_voip["access1"]


def test_ap_ssids_match_vlans():
    _, configs = _build()
    ap = configs["ap1"]
    assert "dot11 ssid ACME-DENTAL-STAFF\n vlan 10" in ap
    assert "dot11 ssid ACME-DENTAL-GUEST\n vlan 20" in ap


def test_every_link_end_is_described():
    plan, configs = _build()
    for link in plan.links:
        for me, peer in ((link.source_id, link.target_id), (link.target_id, link.source_id)):
            if me in configs and plan_type(plan, peer) != "wan_uplink":
                assert f"to {peer}\n" in configs[me], (me, peer)


def plan_type(plan, node_id):
    return next(n.node_type for n in plan.nodes if n.node_id == node_id)


@pytest.mark.parametrize(
    "user_count, redundancy, base",
    list(itertools.product(
        [1, 62, 600],
        list(RedundancyLevel),
        [None, "10.0.0.0/8", "192.168.0.0/16"],
    )),
)
def test_generates_for_varied_specs(user_count, redundancy, base):
    _, configs = _build(user_count=user_count, redundancy=redundancy, preferred_base_cidr=base)
    addrs = [ip for text in configs.values() for ip, _ in _ip_addresses(text)]
    assert len(addrs) == len(set(addrs))

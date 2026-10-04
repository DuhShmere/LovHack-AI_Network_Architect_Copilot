"""
Config audit: NetworkPlan + generated configs -> list[ValidationCheck].

Owner: Nyles. validate_plan() proves the *design* is sound; this proves the
*configs we ship* actually implement it. It re-reads the Cisco-style text
config_gen produced -- it never peeks at config_gen's internals -- so a bug
in config_gen (or a hand edit) shows up as a failed check:

  - guest_isolation_enforced: every L3 interface on the guest subnet has an
    inbound ACL, and evaluating that ACL rule by rule denies guest traffic
    to every internal subnet and infrastructure address while still letting
    it reach the internet.
  - gateways_consistent: every VLAN's SVIs, HSRP virtual IP, DHCP
    default-router and device default-gateways agree on one gateway.
  - no_ip_conflicts: no static address is configured twice, and no DHCP
    pool can lease an address that's statically assigned.
  - routing_complete: tracing the static routes hop by hop, every VLAN can
    reach the internet and every edge router can route back to every VLAN
    (over every equal-prefix next hop, so failover routes are traced too),
    and NAT covers every VLAN.

Like the validator, checks never raise: anything unparseable is a failure.
"""

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field

from shared.schema import DeviceConfig, NetworkPlan, ValidationCheck

INTERNET_PROBE = ipaddress.ip_network("8.8.8.8/32")
ANY = ipaddress.ip_network("0.0.0.0/0")
MAX_HOPS = 16


def audit_configs(plan: NetworkPlan, configs: list[DeviceConfig]) -> list[ValidationCheck]:
    devices = {c.node_id: parse_config(c.config_text) for c in configs}
    checks = [
        ("guest_isolation_enforced", lambda: _check_guest_isolation(plan, devices)),
        ("gateways_consistent", lambda: _check_gateways(plan, devices)),
        ("no_ip_conflicts", lambda: _check_ip_conflicts(devices)),
        ("routing_complete", lambda: _check_routing(plan, devices)),
    ]
    results = []
    for name, run in checks:
        try:
            result = run()
        except (ValueError, KeyError, IndexError) as e:  # garbled config text
            result = _check(name, False, f"Could not audit the configs: {e}")
        if result is not None:
            results.append(result)
    return results


def _check(name: str, passed: bool, detail: str) -> ValidationCheck:
    return ValidationCheck(check_name=name, passed=passed, detail=detail)


def _net(ip: str, mask: str) -> ipaddress.IPv4Network:
    """'10.0.0.0', '255.255.255.0' or '0.0.0.255' (wildcard) -> network."""
    return ipaddress.ip_network(f"{ip}/{mask}", strict=False)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class _AclEntry:
    action: str  # "permit" | "deny"
    src: ipaddress.IPv4Network
    dst: ipaddress.IPv4Network
    all_traffic: bool  # False if it only matches some protocols/ports


@dataclass
class _Route:
    prefix: ipaddress.IPv4Network
    next_hop: str  # an address, or "dhcp" (the ISP's gateway)
    distance: int = 1
    track: int | None = None  # only installed while this track object is up


@dataclass
class ParsedConfig:
    blocks: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    globals: list[str] = field(default_factory=list)
    acls: dict[str, list[_AclEntry]] = field(default_factory=dict)
    routes: list[_Route] = field(default_factory=list)
    addresses: list[tuple[str, ipaddress.IPv4Interface]] = field(default_factory=list)  # (interface, addr)
    sla_targets: dict[int, ipaddress.IPv4Address] = field(default_factory=dict)  # ip sla id -> probed address
    sla_sources: dict[int, str] = field(default_factory=dict)  # ip sla id -> source interface
    tracks: dict[int, int] = field(default_factory=dict)  # track id -> ip sla id

    def interface_body(self, name: str) -> list[str]:
        return self.blocks.get(f"interface {name}", [])

    def connected(self, addr) -> bool:
        return any(addr in iface.network for _, iface in self.addresses)

    def inbound_acl(self, iface: str) -> str | None:
        return next(
            (m.group(1) for line in self.interface_body(iface)
             if (m := re.fullmatch(r"ip access-group (\S+) in", line))),
            None,
        )

    def hsrp_priority(self, iface: str) -> int:
        return next(
            (int(m.group(1)) for line in self.interface_body(iface)
             if (m := re.fullmatch(r"standby \d+ priority (\d+)", line))),
            100,
        )


def parse_config(text: str) -> ParsedConfig:
    dev = ParsedConfig()
    header = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line == "!":
            header = None
            continue
        if not line or line == "end":
            continue
        if line.startswith(" "):
            if header:
                dev.blocks[header].append(line.strip())
            continue
        header = line
        dev.globals.append(line)
        dev.blocks[header]  # register blocks with no body too

    for header, body in dev.blocks.items():
        if header.startswith("interface "):
            for line in body:
                m = re.fullmatch(r"ip address (\S+) (\S+)", line)
                if m and m.group(1) != "dhcp":
                    try:
                        iface = ipaddress.ip_interface(f"{m.group(1)}/{m.group(2)}")
                    except ValueError:
                        continue
                    dev.addresses.append((header.split(" ", 1)[1], iface))
        m = re.fullmatch(r"ip access-list (standard|extended) (\S+)", header)
        if m:
            dev.acls[m.group(2)] = [
                e for e in (_parse_ace(line, m.group(1) == "standard") for line in body) if e
            ]

        m = re.fullmatch(r"ip sla (\d+)", header)
        if m:
            for line in body:
                if (probe := re.fullmatch(r"icmp-echo (\S+)(?: source-interface (\S+))?.*", line)):
                    try:
                        dev.sla_targets[int(m.group(1))] = ipaddress.ip_address(probe.group(1))
                    except ValueError:
                        continue
                    if probe.group(2):
                        dev.sla_sources[int(m.group(1))] = probe.group(2)

    for line in dev.globals:
        m = re.fullmatch(r"ip route (\S+) (\S+) (\S+)(?: (\d+))?(?: track (\d+))?", line)
        if m:
            try:
                dev.routes.append(_Route(
                    _net(m.group(1), m.group(2)), m.group(3),
                    distance=int(m.group(4) or 1),
                    track=int(m.group(5)) if m.group(5) else None,
                ))
            except ValueError:
                pass
        if (t := re.fullmatch(r"track (\d+) ip sla (\d+) reachability", line)):
            dev.tracks[int(t.group(1))] = int(t.group(2))
    return dev


def _parse_address(tokens: list[str]) -> tuple[ipaddress.IPv4Network, list[str]]:
    if tokens[0] == "any":
        return ANY, tokens[1:]
    if tokens[0] == "host":
        return ipaddress.ip_network(f"{tokens[1]}/32"), tokens[2:]
    return _net(tokens[0], tokens[1]), tokens[2:]


def _parse_ace(line: str, standard: bool) -> _AclEntry | None:
    """One access-control entry. Unparseable lines return None (and so can't
    count as a deny -- a garbled rule never makes the audit *more* lenient)."""
    tokens = line.split()
    if not tokens or tokens[0] not in ("permit", "deny"):
        return None
    action, rest = tokens[0], tokens[1:]
    try:
        if standard:
            src, rest = _parse_address(rest)
            return _AclEntry(action, src, ANY, all_traffic=True)
        proto, rest = rest[0], rest[1:]
        src, rest = _parse_address(rest)
        if rest and rest[0] in ("eq", "neq", "lt", "gt"):
            return _AclEntry(action, src, _parse_address(rest[2:])[0], all_traffic=False)
        if rest and rest[0] == "range":
            return _AclEntry(action, src, _parse_address(rest[3:])[0], all_traffic=False)
        dst, rest = _parse_address(rest)
    except (IndexError, ValueError):
        return None
    qualifiers = [t for t in rest if t != "log"]
    return _AclEntry(action, src, dst, all_traffic=proto == "ip" and not qualifiers)


# ---------------------------------------------------------------------------
# ACL evaluation
# ---------------------------------------------------------------------------

def acl_verdict(entries: list[_AclEntry], src, dst) -> str:
    """How an ACL treats ALL traffic from src to dst: "permit", "deny", or
    "mixed" (some of it gets through, some doesn't).

    First match wins, as on IOS. An entry that only partly covers the flow
    (a sub-range, or one protocol) decides only part of it, so we remember
    its action; the flow is uniform only if the entry that finally covers
    everything (or the implicit deny) agrees with all of those.
    """
    partial = set()
    for e in entries:
        if not (e.src.overlaps(src) and e.dst.overlaps(dst)):
            continue
        if e.all_traffic and src.subnet_of(e.src) and dst.subnet_of(e.dst):
            return e.action if partial <= {e.action} else "mixed"
        partial.add(e.action)
    return "deny" if partial <= {"deny"} else "mixed"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_guest_isolation(plan: NetworkPlan, devices: dict[str, ParsedConfig]) -> ValidationCheck | None:
    name = "guest_isolation_enforced"
    if not plan.spec.guest_wifi_isolated:
        return None
    guest = next((v for v in plan.vlans if v.name == "guest"), None)
    if guest is None:
        return _check(name, False, "Guest isolation requested but there is no guest VLAN to enforce it on.")
    guest_net = ipaddress.ip_network(guest.subnet_cidr)

    internal = [ipaddress.ip_network(v.subnet_cidr) for v in plan.vlans if v is not guest]
    infra = sorted({
        iface.ip for dev in devices.values() for _, iface in dev.addresses
        if iface.ip not in guest_net and not any(iface.ip in n for n in internal)
    })
    protected = internal + [ipaddress.ip_network(f"{ip}/32") for ip in infra]

    gateways = [
        (node_id, iface_name) for node_id, dev in devices.items()
        for iface_name, iface in dev.addresses if iface.ip in guest_net
    ]
    if not gateways:
        return _check(name, False, f"No device has an interface on guest subnet {guest_net}, so nothing filters it.")

    problems, acl_names = [], set()
    for node_id, iface_name in gateways:
        dev = devices[node_id]
        acl_name = dev.inbound_acl(iface_name)
        where = f"{node_id} {iface_name}"
        if acl_name is None:
            problems.append(f"{where} routes guest traffic with no inbound ACL")
            continue
        if acl_name not in dev.acls:
            problems.append(f"{where} applies ACL {acl_name}, which isn't defined (so it permits everything)")
            continue
        acl_names.add(acl_name)
        entries = dev.acls[acl_name]
        leaks = [str(p) for p in protected if acl_verdict(entries, guest_net, p) != "deny"]
        if leaks:
            problems.append(f"{where} ({acl_name}) lets guests reach {', '.join(leaks)}")
        if acl_verdict(entries, guest_net, INTERNET_PROBE) != "permit":
            problems.append(f"{where} ({acl_name}) blocks guests from the internet")

    if problems:
        return _check(name, False, "; ".join(problems) + ".")
    return _check(
        name, True,
        f"Evaluated {', '.join(sorted(acl_names))} rule by rule on "
        f"{', '.join(f'{n} {i}' for n, i in gateways)}: guest {guest_net} is denied to all "
        f"{len(internal)} internal subnets and {len(infra)} infrastructure addresses, "
        f"and still reaches the internet.",
    )


def _check_gateways(plan: NetworkPlan, devices: dict[str, ParsedConfig]) -> ValidationCheck:
    name = "gateways_consistent"
    cores = [n.node_id for n in plan.nodes if n.node_type == "core_switch"]
    pools = _dhcp_pools(devices)
    problems = []
    gateway_of = {}

    for v in plan.vlans:
        net = ipaddress.ip_network(v.subnet_cidr)
        svi = f"Vlan{v.vlan_id}"
        svi_ips, vips = [], set()
        for core in cores:
            dev = devices.get(core)
            addr = next((i for n, i in dev.addresses if n == svi), None) if dev else None
            if addr is None:
                problems.append(f"{core} has no {svi} interface")
                continue
            if addr.network != net:
                problems.append(f"{core} {svi} is {addr.with_prefixlen}, not on {net}")
            svi_ips.append(addr.ip)
            vips |= {
                ipaddress.ip_address(m.group(1)) for line in dev.interface_body(svi)
                if (m := re.fullmatch(r"standby \d+ ip (\S+)", line))
            }
        if not svi_ips:
            continue

        if len(cores) > 1:
            if len(vips) != 1:
                problems.append(f"VLAN {v.vlan_id} cores disagree on the HSRP virtual IP ({sorted(map(str, vips)) or 'none'})")
                continue
            gateway = vips.pop()
            if gateway not in net:
                problems.append(f"VLAN {v.vlan_id} HSRP virtual IP {gateway} is outside {net}")
        else:
            gateway = svi_ips[0]
        gateway_of[v.vlan_id] = gateway

        for owner, pool in pools:
            if pool["network"] == net and pool.get("default-router") != gateway:
                problems.append(
                    f"DHCP pool {pool['name']} on {owner} hands out gateway "
                    f"{pool.get('default-router')}, but VLAN {v.vlan_id}'s gateway is {gateway}"
                )

    mgmt = next((v for v in plan.vlans if v.name == "management"), None)
    if mgmt and mgmt.vlan_id in gateway_of:
        expected = gateway_of[mgmt.vlan_id]
        for node_id, dev in devices.items():
            for line in dev.globals:
                m = re.fullmatch(r"ip default-gateway (\S+)", line)
                if m and ipaddress.ip_address(m.group(1)) != expected:
                    problems.append(f"{node_id} default-gateway is {m.group(1)}, management gateway is {expected}")

    if problems:
        return _check(name, False, "; ".join(problems) + ".")
    kind = "HSRP virtual IP" if len(cores) > 1 else "core SVI"
    return _check(
        name, True,
        f"All {len(gateway_of)} VLANs have one gateway ({kind}) that every core SVI, "
        f"DHCP pool and managed device's default-gateway agrees on.",
    )


def _dhcp_pools(devices: dict[str, ParsedConfig]) -> list[tuple[str, dict]]:
    pools = []
    for node_id, dev in devices.items():
        for header, body in dev.blocks.items():
            m = re.fullmatch(r"ip dhcp pool (\S+)", header)
            if not m:
                continue
            pool = {"name": m.group(1)}
            for line in body:
                if (n := re.fullmatch(r"network (\S+) (\S+)", line)):
                    pool["network"] = _net(n.group(1), n.group(2))
                elif (r := re.fullmatch(r"default-router (\S+)", line)):
                    pool["default-router"] = ipaddress.ip_address(r.group(1))
            pools.append((node_id, pool))
    return pools


def _excluded(dev: ParsedConfig) -> list[tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]]:
    ranges = []
    for line in dev.globals:
        m = re.fullmatch(r"ip dhcp excluded-address (\S+)(?: (\S+))?", line)
        if m:
            lo = ipaddress.ip_address(m.group(1))
            ranges.append((lo, ipaddress.ip_address(m.group(2)) if m.group(2) else lo))
    return ranges


def _check_ip_conflicts(devices: dict[str, ParsedConfig]) -> ValidationCheck:
    name = "no_ip_conflicts"
    owners = defaultdict(list)
    vips = set()
    for node_id, dev in devices.items():
        for iface_name, iface in dev.addresses:
            owners[iface.ip].append(f"{node_id} {iface_name}")
        for body in dev.blocks.values():
            for line in body:
                if (m := re.fullmatch(r"standby \d+ ip (\S+)", line)):
                    vips.add(ipaddress.ip_address(m.group(1)))
    for vip in vips:  # shared by the cores by design, so it counts once
        owners[vip].append("the HSRP virtual IP")

    problems = [f"{ip} is configured on {' and '.join(who)}" for ip, who in sorted(owners.items()) if len(who) > 1]

    pools = _dhcp_pools(devices)
    for owner, pool in pools:
        net = pool.get("network")
        if net is None:
            continue
        excluded = _excluded(devices[owner])
        leasable = [
            ip for ip in owners
            if ip in net and ip not in (net.network_address, net.broadcast_address)
            and not any(lo <= ip <= hi for lo, hi in excluded)
        ]
        if leasable:
            problems.append(
                f"DHCP pool {pool['name']} on {owner} can lease {', '.join(map(str, sorted(leasable)))}, "
                f"which {'is' if len(leasable) == 1 else 'are'} statically assigned"
            )
    served = defaultdict(list)
    for owner, pool in pools:
        if "network" in pool:
            served[pool["network"]].append(owner)
    problems += [f"{net} is served by DHCP on {', '.join(who)}" for net, who in served.items() if len(who) > 1]

    if problems:
        return _check(name, False, "; ".join(problems) + ".")
    return _check(
        name, True,
        f"All {len(owners)} static addresses (including HSRP virtual IPs) are unique, and none of "
        f"the {len(pools)} DHCP pools can lease one of them.",
    )


def _check_routing(plan: NetworkPlan, devices: dict[str, ParsedConfig]) -> ValidationCheck:
    name = "routing_complete"
    cores = [n.node_id for n in plan.nodes if n.node_type == "core_switch" and n.node_id in devices]
    routers = [n.node_id for n in plan.nodes if n.node_type == "router" and n.node_id in devices]
    vlan_nets = [(v, ipaddress.ip_network(v.subnet_cidr)) for v in plan.vlans]
    owner_of = {iface.ip: node_id for node_id, dev in devices.items() for _, iface in dev.addresses}
    problems = []

    if not cores or not routers:
        return _check(name, False, f"Need a core switch and an edge router to route, found {len(cores)} and {len(routers)}.")

    for core in cores:
        err = _trace(devices, owner_of, core, INTERNET_PROBE.network_address)
        if err:
            problems.append(f"outbound from {core}: {err}")

    for router in routers:
        nat = devices[router].acls.get("NAT-INSIDE")
        for v, net in vlan_nets:
            err = _trace(devices, owner_of, router, net.network_address + 1)
            if err:
                problems.append(f"{router} -> VLAN {v.vlan_id}: {err}")
            if nat is None or acl_verdict(nat, net, ANY) != "permit":
                problems.append(f"{router} doesn't NAT VLAN {v.vlan_id} ({net})")

    if problems:
        return _check(name, False, "; ".join(problems) + ".")
    return _check(
        name, True,
        f"Traced the static routes hop by hop: {', '.join(cores)} reach the internet, and "
        f"{', '.join(routers)} route and NAT all {len(vlan_nets)} VLANs, over every next hop "
        f"including failover routes.",
    )


def _trace(devices, owner_of, start: str, dst, path=()) -> str | None:
    """Follow the longest-prefix routes from start toward dst, branching on
    every next hop that prefix lists. None if every branch is delivered
    (dst on a connected interface, or handed to the ISP), else why not."""
    path = path + (start,)
    dev = devices[start]
    if dev.connected(dst):
        return None
    if len(path) > MAX_HOPS or start in path[:-1]:
        return "routing loop via " + " -> ".join(path)
    matches = [r for r in dev.routes if dst in r.prefix]
    if not matches:
        return f"{start} has no route to {dst} (path {' -> '.join(path)})"
    longest = max(r.prefix.prefixlen for r in matches)
    for route in (r for r in matches if r.prefix.prefixlen == longest):
        if route.next_hop == "dhcp":
            continue  # default route learned from the ISP: off our network
        try:
            hop = ipaddress.ip_address(route.next_hop)
        except ValueError:
            return f"{start} has an unparseable next hop {route.next_hop!r}"
        if not dev.connected(hop):
            return f"{start}'s next hop {hop} isn't on any of its interfaces"
        nxt = owner_of.get(hop)
        if nxt is None:
            return f"{start}'s next hop {hop} isn't configured on any device"
        err = _trace(devices, owner_of, nxt, dst, path)
        if err:
            return err
    return None

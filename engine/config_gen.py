"""
Cisco-style config generator: NetworkPlan -> list[DeviceConfig].

Owner: Nyles. Day 5 task. Only run this on a plan that has already
passed validate_plan() -- don't generate configs for an invalid design.

Layout the configs assume:
  - Core switch(es) do inter-VLAN routing via SVIs (HSRP if there are two)
    and serve DHCP; the guest SVI carries an isolation ACL.
  - router <-> firewall <-> core hops are routed /30s carved from a
    transit block that doesn't collide with any VLAN.
  - Edge routers NAT the internal subnets out their ISP-facing port.
  - Access switches trunk to every core; remaining ports are staff access
    ports (with a voice VLAN if one exists). APs trunk staff + guest.

Stretch goal: feed the output into containerlab to actually boot and
verify the config instead of just printing text that looks plausible.
"""

import ipaddress
import re

from shared.schema import NetworkPlan, DeviceConfig

ACCESS_SWITCH_PORTS = 48
L3_NODE_TYPES = {"router", "firewall", "core_switch"}
TRANSIT_CANDIDATES = ["10.255.255.0/24", "172.31.255.0/24", "192.168.255.0/24"]
HSRP_GROUP = 1
SLA_ID = 1
# Probed only to track the primary WAN path; Quad9, so it isn't a DNS server
# the DHCP pools hand out (traffic to it is pinned to the primary path).
SLA_PROBE_TARGET = "9.9.9.9"


def generate_configs(plan: NetworkPlan) -> list[DeviceConfig]:
    ctx = _Context(plan)
    renderers = {
        "router": _render_router,
        "firewall": _render_firewall,
        "core_switch": _render_core,
        "access_switch": _render_access,
        "ap": _render_ap,
    }
    configs = []
    for node in plan.nodes:
        render = renderers.get(node.node_type)
        if render:  # wan_uplink etc. are the ISP's gear, not ours
            configs.append(DeviceConfig(node_id=node.node_id, config_text=render(node, ctx)))
    return configs


# ---------------------------------------------------------------------------
# Shared addressing / port bookkeeping
# ---------------------------------------------------------------------------

class _Context:
    def __init__(self, plan: NetworkPlan):
        self.plan = plan
        self.types = {n.node_id: n.node_type for n in plan.nodes}
        self.labels = {n.node_id: n.label for n in plan.nodes}
        self.vlans = sorted(plan.vlans, key=lambda v: v.vlan_id)
        self.nets = {v.vlan_id: ipaddress.ip_network(v.subnet_cidr) for v in self.vlans}
        self.by_name = {v.name: v for v in self.vlans}
        self.cores = [n.node_id for n in plan.nodes if n.node_type == "core_switch"]
        self.routers = [n.node_id for n in plan.nodes if n.node_type == "router"]

        # Interface per (node, peer), numbered in link order.
        self.ports: dict[tuple[str, str], str] = {}
        self.neighbors: dict[str, list[str]] = {n.node_id: [] for n in plan.nodes}
        for link in plan.links:
            for me, peer in ((link.source_id, link.target_id), (link.target_id, link.source_id)):
                self.neighbors[me].append(peer)
                self.ports[(me, peer)] = self._iface(me, len(self.neighbors[me]))

        # Routed /30 per L3<->L3 link (core<->core is a trunk, not routed).
        self.transit = transit = self._transit_block()
        self.p2p: dict[tuple[str, str], ipaddress.IPv4Interface] = {}
        subnets = transit.subnets(new_prefix=30)
        for link in plan.links:
            a, b = link.source_id, link.target_id
            if {self.types.get(a), self.types.get(b)} <= L3_NODE_TYPES and not (
                self.types[a] == self.types[b] == "core_switch"
            ):
                net = next(subnets)
                hosts = list(net.hosts())
                self.p2p[(a, b)] = ipaddress.ip_interface(f"{hosts[0]}/30")
                self.p2p[(b, a)] = ipaddress.ip_interface(f"{hosts[1]}/30")

        # Management addressing: gateway (or HSRP VIP) is .1, cores next,
        # then every other managed device.
        self.mgmt = self.by_name.get("management")
        self.mgmt_ip: dict[str, ipaddress.IPv4Address] = {}
        if self.mgmt:
            hosts = self.nets[self.mgmt.vlan_id].hosts()
            next(hosts)  # .1 = gateway / VIP
            if len(self.cores) == 1:
                self.mgmt_ip[self.cores[0]] = self.gateway(self.mgmt.vlan_id)
            else:
                for core in self.cores:
                    self.mgmt_ip[core] = next(hosts)
            for n in plan.nodes:
                if n.node_type in ("access_switch", "ap"):
                    self.mgmt_ip[n.node_id] = next(hosts)

    def _iface(self, node_id: str, index: int) -> str:
        if self.types[node_id] in ("access_switch", "core_switch"):
            return f"GigabitEthernet1/0/{index}"
        return f"GigabitEthernet0/{index - 1}"

    def _transit_block(self) -> ipaddress.IPv4Network:
        for cidr in TRANSIT_CANDIDATES:
            block = ipaddress.ip_network(cidr)
            if not any(block.overlaps(net) for net in self.nets.values()):
                return block
        raise ValueError("No free transit block for routed links")

    def gateway(self, vlan_id: int) -> ipaddress.IPv4Address:
        return next(self.nets[vlan_id].hosts())

    def core_svi_ip(self, core: str, vlan_id: int) -> ipaddress.IPv4Address:
        if len(self.cores) == 1:
            return self.gateway(vlan_id)
        return self.nets[vlan_id].network_address + 2 + self.cores.index(core)

    def uplink_ip(self, me: str, peer: str) -> ipaddress.IPv4Address:
        """The peer's address on the routed link between me and peer."""
        return self.p2p[(peer, me)].ip

    def neighbors_of_type(self, node_id: str, node_type: str) -> list[str]:
        return [p for p in self.neighbors[node_id] if self.types.get(p) == node_type]

    def user_vlans(self) -> list:
        return [v for v in self.vlans if v.name != "management"]


def _mask(net) -> str:
    return f"{net.network_address} {net.netmask}"


def _wild(net) -> str:
    return f"{net.network_address} {net.hostmask}"


def _vlan_list(vlans) -> str:
    return ",".join(str(v.vlan_id) for v in vlans)


def _header(node, ctx: _Context) -> list[str]:
    return [
        f"hostname {node.node_id.upper()}",
        "!",
        f"banner motd ^ {ctx.plan.spec.org_name} -- {node.label}. Authorized access only. ^",
        "no ip domain-lookup",
        "!",
    ]


def _routed_iface(me: str, peer: str, ctx: _Context, extra: list[str] = (), label: str = "") -> list[str]:
    addr = ctx.p2p[(me, peer)]
    return [
        f"interface {ctx.ports[(me, peer)]}",
        f" description {label + ' - ' if label else ''}routed link to {peer}",
        " no switchport" if ctx.types[me] == "core_switch" else None,
        f" ip address {addr.ip} {addr.netmask}",
        *extra,
        " no shutdown",
        "!",
    ]


def _trunk_iface(me: str, peer: str, ctx: _Context, vlans, native=None) -> list[str]:
    return [
        f"interface {ctx.ports[(me, peer)]}",
        f" description trunk to {peer}",
        " switchport mode trunk",
        f" switchport trunk native vlan {native}" if native else None,
        f" switchport trunk allowed vlan {_vlan_list(vlans)}",
        " no shutdown",
        "!",
    ]


def _vlan_defs(ctx: _Context) -> list[str]:
    lines = []
    for v in ctx.vlans:
        lines += [f"vlan {v.vlan_id}", f" name {v.name.upper()}"]
    return lines + ["!"]


def _finish(lines: list) -> str:
    return "\n".join(l for l in lines if l is not None) + "\nend\n"


# ---------------------------------------------------------------------------
# Per-device renderers
# ---------------------------------------------------------------------------

def _render_router(node, ctx: _Context) -> str:
    me = node.node_id
    lines = _header(node, ctx)
    for peer in ctx.neighbors[me]:
        if ctx.types[peer] == "wan_uplink":
            lines += [
                f"interface {ctx.ports[(me, peer)]}",
                f" description WAN - {ctx.labels[peer]}",
                " ip address dhcp",
                " ip nat outside",
                " no shutdown",
                "!",
            ]
        elif (me, peer) in ctx.p2p:
            lines += _routed_iface(me, peer, ctx, [" ip nat inside"])

    wan_ports = [ctx.ports[(me, p)] for p in ctx.neighbors_of_type(me, "wan_uplink")]
    firewall = next(iter(ctx.neighbors_of_type(me, "firewall")), None)
    lines += ["ip access-list standard NAT-INSIDE"]
    lines += [f" permit {_wild(ctx.nets[v.vlan_id])}" for v in ctx.vlans]
    lines += ["!"]
    if wan_ports:
        lines += [f"ip nat inside source list NAT-INSIDE interface {wan_ports[0]} overload", "!"]
        lines += ["ip route 0.0.0.0 0.0.0.0 dhcp"]
    if firewall:
        next_hop = ctx.uplink_ip(me, firewall)
        lines += [f"ip route {_mask(ctx.nets[v.vlan_id])} {next_hop}" for v in ctx.vlans]
    return _finish(lines + ["!"])


def _render_firewall(node, ctx: _Context) -> str:
    me = node.node_id
    lines = _header(node, ctx)
    routers = ctx.neighbors_of_type(me, "router")
    cores = ctx.neighbors_of_type(me, "core_switch")
    for peer in routers:
        lines += _routed_iface(me, peer, ctx, label="OUTSIDE")
    for peer in cores:
        lines += _routed_iface(me, peer, ctx, label="INSIDE")

    # Internal subnets live behind the core(s); ECMP across both if dual.
    for v in ctx.vlans:
        for core in cores:
            lines.append(f"ip route {_mask(ctx.nets[v.vlan_id])} {ctx.uplink_ip(me, core)}")
    # Primary default via router1, floating backup via router2 (dual WAN).
    # A floating static only takes over when router1's link drops, not when
    # ISP A dies behind a healthy router1 -- so with a backup, the primary
    # route is tracked by an IP SLA probe pinned to the primary path.
    if len(routers) > 1:
        primary_port = ctx.ports[(me, routers[0])]
        lines += [
            f"ip sla {SLA_ID}",
            f" icmp-echo {SLA_PROBE_TARGET} source-interface {primary_port}",
            " frequency 10",
            "!",
            f"ip sla schedule {SLA_ID} life forever start-time now",
            f"track {SLA_ID} ip sla {SLA_ID} reachability",
            "!",
            f"ip route {SLA_PROBE_TARGET} 255.255.255.255 {ctx.uplink_ip(me, routers[0])}",
        ]
    for i, router in enumerate(routers):
        suffix = f" track {SLA_ID}" if i == 0 and len(routers) > 1 else "" if i == 0 else f" {10 * i}"
        lines.append(f"ip route 0.0.0.0 0.0.0.0 {ctx.uplink_ip(me, router)}{suffix}")
    lines += ["!"]

    # Only return traffic from outside; anything from inside may go out.
    outside_ports = [ctx.ports[(me, r)] for r in routers]
    lines += [
        "ip access-list extended OUTSIDE-IN",
        " permit tcp any any established",
        " permit udp any eq domain any",
        " permit icmp any any echo-reply",
        " deny ip any any log",
        "!",
    ]
    for port in outside_ports:
        lines += [f"interface {port}", " ip access-group OUTSIDE-IN in", "!"]
    return _finish(lines)


def _render_core(node, ctx: _Context) -> str:
    me = node.node_id
    dual = len(ctx.cores) > 1
    primary = me == ctx.cores[0]
    lines = _header(node, ctx) + ["ip routing", "!"] + _vlan_defs(ctx)

    guest = ctx.by_name.get("guest")
    isolate_guest = guest and ctx.plan.spec.guest_wifi_isolated

    for v in ctx.vlans:
        net = ctx.nets[v.vlan_id]
        lines += [
            f"interface Vlan{v.vlan_id}",
            f" description {v.purpose}",
            f" ip address {ctx.core_svi_ip(me, v.vlan_id)} {net.netmask}",
        ]
        if dual:
            lines += [
                f" standby {HSRP_GROUP} ip {ctx.gateway(v.vlan_id)}",
                f" standby {HSRP_GROUP} priority {110 if primary else 100}",
                f" standby {HSRP_GROUP} preempt",
            ]
        if isolate_guest and v.vlan_id == guest.vlan_id:
            lines.append(" ip access-group GUEST-ISOLATION in")
        lines += [" no shutdown", "!"]

    if isolate_guest:
        guest_net = ctx.nets[guest.vlan_id]
        lines += ["ip access-list extended GUEST-ISOLATION"]
        lines += [
            f" deny ip {_wild(guest_net)} {_wild(ctx.nets[v.vlan_id])}"
            for v in ctx.vlans if v.vlan_id != guest.vlan_id
        ]
        lines += [f" deny ip {_wild(guest_net)} {_wild(ctx.transit)}"]
        lines += [" permit ip any any", "!"]

    # DHCP lives on the primary core only so the two don't hand out duplicates.
    if primary:
        for v in ctx.user_vlans():
            net = ctx.nets[v.vlan_id]
            reserved_end = net.network_address + min(10, net.num_addresses - 2)
            lines += [
                f"ip dhcp excluded-address {ctx.gateway(v.vlan_id)} {reserved_end}",
                f"ip dhcp pool {v.name.upper()}",
                f" network {_mask(net)}",
                f" default-router {ctx.gateway(v.vlan_id)}",
                " dns-server 1.1.1.1 8.8.8.8",
                "!",
            ]

    for peer in ctx.neighbors[me]:
        if (me, peer) in ctx.p2p:
            lines += _routed_iface(me, peer, ctx)
        elif ctx.types[peer] in ("access_switch", "core_switch"):
            lines += _trunk_iface(me, peer, ctx, ctx.vlans)

    for fw in ctx.neighbors_of_type(me, "firewall"):
        lines.append(f"ip route 0.0.0.0 0.0.0.0 {ctx.uplink_ip(me, fw)}")
    return _finish(lines + ["!"])


def _render_access(node, ctx: _Context) -> str:
    me = node.node_id
    lines = _header(node, ctx) + _vlan_defs(ctx)

    for peer in ctx.neighbors[me]:
        if ctx.types[peer] == "core_switch":
            lines += _trunk_iface(me, peer, ctx, ctx.vlans)
        elif ctx.types[peer] == "ap":
            ap_vlans = [v for v in ctx.vlans if v.name in ("staff", "guest", "management")]
            native = ctx.mgmt.vlan_id if ctx.mgmt else None
            lines += _trunk_iface(me, peer, ctx, ap_vlans, native=native)

    first_user_port = len(ctx.neighbors[me]) + 1
    staff = ctx.by_name.get("staff")
    voip = ctx.by_name.get("voip")
    if staff and first_user_port <= ACCESS_SWITCH_PORTS:
        lines += [
            f"interface range GigabitEthernet1/0/{first_user_port}-{ACCESS_SWITCH_PORTS}",
            " description staff access ports",
            " switchport mode access",
            f" switchport access vlan {staff.vlan_id}",
            f" switchport voice vlan {voip.vlan_id}" if voip else None,
            " spanning-tree portfast",
            " spanning-tree bpduguard enable",
            " no shutdown",
            "!",
        ]
    lines += _mgmt_svi(me, ctx)
    return _finish(lines)


def _render_ap(node, ctx: _Context) -> str:
    me = node.node_id
    lines = _header(node, ctx)
    ssid_base = re.sub(r"[^A-Za-z0-9]+", "-", ctx.plan.spec.org_name).strip("-").upper()[:24]
    for name in ("staff", "guest"):
        v = ctx.by_name.get(name)
        if v:
            lines += [
                f"dot11 ssid {ssid_base}-{name.upper()}",
                f" vlan {v.vlan_id}",
                " authentication open",
                " authentication key-management wpa version 2",
                " guest-mode" if name == "guest" else None,
                "!",
            ]
    for peer in ctx.neighbors[me]:
        lines += [f"interface {ctx.ports[(me, peer)]}", f" description uplink to {peer}", " no shutdown", "!"]
    if me in ctx.mgmt_ip:
        net = ctx.nets[ctx.mgmt.vlan_id]
        lines += [
            "interface BVI1",
            f" ip address {ctx.mgmt_ip[me]} {net.netmask}",
            "!",
            f"ip default-gateway {ctx.gateway(ctx.mgmt.vlan_id)}",
        ]
    return _finish(lines + ["!"])


def _mgmt_svi(me: str, ctx: _Context) -> list[str]:
    if me not in ctx.mgmt_ip:
        return []
    net = ctx.nets[ctx.mgmt.vlan_id]
    return [
        f"interface Vlan{ctx.mgmt.vlan_id}",
        " description management",
        f" ip address {ctx.mgmt_ip[me]} {net.netmask}",
        " no shutdown",
        "!",
        f"ip default-gateway {ctx.gateway(ctx.mgmt.vlan_id)}",
        "!",
    ]

"""
Simulator: what traffic actually does in a generated design -- and what
happens when devices fail.

Owner: Nyles. Packets are walked through the generated Cisco-style configs
(parsed by engine/config_audit, never read from config_gen's internals):
the inbound ACL at every hop, the static route that would really be
installed (next hop alive, longest prefix, lowest distance, tracked routes
only while their IP SLA probe gets through), HSRP failover between cores,
and NAT on the way out. Nothing here is an LLM guess.

  - simulate(plan, failed): every VLAN to every other VLAN and to the
    internet (round trip), with the hop-by-hop path or the reason it's
    blocked, given a set of failed devices.
  - resilience(plan): every single-device failure, and which ones cut a
    whole VLAN off from the internet (single points of failure).
"""

import ipaddress
from collections import defaultdict, deque

from pydantic import BaseModel

from shared.schema import NetworkPlan
from engine.config_gen import generate_configs
from engine.config_audit import ANY, ParsedConfig, acl_verdict, parse_config

INTERNET = "internet"
INTERNET_PROBE = ipaddress.ip_address("8.8.8.8")
MAX_HOPS = 16
SWITCH_TYPES = {"core_switch", "access_switch"}


class SimulationError(ValueError):
    """Unknown device names, or a plan the config generator can't render."""


class FlowResult(BaseModel):
    source: str  # VLAN name
    destination: str  # VLAN name or "internet"
    allowed: bool
    path: list[str]  # node ids hop by hop, ending in the destination
    reason: str


class SimulationReport(BaseModel):
    failed: list[str]
    flows: list[FlowResult]
    stranded: list[str]  # still powered, but cut off from every core switch
    summary: str


class FailureScenario(BaseModel):
    failed: str
    label: str
    node_type: str
    vlans_cut_off: list[str]  # VLANs that lose the internet entirely
    stranded: list[str]
    single_point_of_failure: bool


class ResilienceReport(BaseModel):
    scenarios: list[FailureScenario]
    single_points_of_failure: list[str]
    summary: str


def simulate(plan: NetworkPlan, failed: list[str] = ()) -> SimulationReport:
    net = _Network(plan, _parsed_configs(plan), failed)
    names = [v.name for v in net.vlans]
    flows = [
        net.flow(src, dst)
        for src in names
        for dst in [n for n in names if n != src] + [INTERNET]
    ]
    stranded = net.stranded()
    online = [f.source for f in flows if f.destination == INTERNET and f.allowed]
    cut = [n for n in names if n not in online]
    prefix = f"With {', '.join(sorted(net.failed))} down" if net.failed else "All devices up"
    summary = f"{prefix}: {len(online)} of {len(names)} VLANs reach the internet"
    summary += f" (cut off: {', '.join(cut)})" if cut else ""
    summary += f"; stranded: {', '.join(stranded)}." if stranded else "."
    return SimulationReport(failed=sorted(net.failed), flows=flows, stranded=stranded, summary=summary)


def resilience(plan: NetworkPlan) -> ResilienceReport:
    devices = _parsed_configs(plan)
    scenarios = []
    for node in plan.nodes:
        if node.node_type == "ap":
            continue  # one AP down is a coverage gap, not an outage
        net = _Network(plan, devices, [node.node_id])
        cut = [v.name for v in net.vlans if not net.flow(v.name, INTERNET).allowed]
        scenarios.append(FailureScenario(
            failed=node.node_id,
            label=node.label,
            node_type=node.node_type,
            vlans_cut_off=cut,
            stranded=net.stranded(),
            single_point_of_failure=bool(cut),
        ))
    spofs = [s.failed for s in scenarios if s.single_point_of_failure]
    if spofs:
        summary = (
            f"{len(spofs)} of {len(scenarios)} single-device failures cut VLANs off the internet: "
            f"{', '.join(spofs)}."
        )
    else:
        summary = f"No single device failure (of {len(scenarios)} tried) cuts any VLAN off the internet."
    return ResilienceReport(scenarios=scenarios, single_points_of_failure=spofs, summary=summary)


def _parsed_configs(plan: NetworkPlan) -> dict[str, ParsedConfig]:
    try:
        return {c.node_id: parse_config(c.config_text) for c in generate_configs(plan)}
    except Exception as e:  # a sabotaged plan the generator can't render
        raise SimulationError(f"Can't simulate this plan, its configs don't generate: {e}") from e


class _Network:
    def __init__(self, plan: NetworkPlan, devices: dict[str, ParsedConfig], failed):
        known = {n.node_id for n in plan.nodes}
        unknown = sorted(set(failed) - known)
        if unknown:
            raise SimulationError(f"Unknown devices: {', '.join(unknown)}")
        self.failed = set(failed)
        self.devices = devices
        self.types = {n.node_id: n.node_type for n in plan.nodes}
        self.vlans = sorted(plan.vlans, key=lambda v: v.vlan_id)
        self.by_name = {v.name: (v, ipaddress.ip_network(v.subnet_cidr)) for v in self.vlans}
        self.adj = defaultdict(set)
        for link in plan.links:
            self.adj[link.source_id].add(link.target_id)
            self.adj[link.target_id].add(link.source_id)
        self.owner = {iface.ip: nid for nid, dev in devices.items() for _, iface in dev.addresses}
        self._track_cache = {}

    def up(self, node_id: str) -> bool:
        return node_id not in self.failed

    # --- Layer 2 ---------------------------------------------------------

    def _switch_reach(self, start: str) -> set[str]:
        """Nodes reachable from start across live switches (where VLANs are trunked)."""
        if not self.up(start):
            return set()
        seen, queue = {start}, deque([start])
        while queue:
            for nxt in self.adj[queue.popleft()]:
                if nxt not in seen and self.up(nxt) and self.types.get(nxt) in SWITCH_TYPES:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def _access_serving(self, core: str) -> str | None:
        """A live access switch that can reach this core, i.e. where the hosts are."""
        reach = self._switch_reach(core)
        return next((n for n in sorted(reach) if self.types[n] == "access_switch"), None)

    def stranded(self) -> list[str]:
        cores = [n for n, t in self.types.items() if t == "core_switch" and self.up(n)]
        reach = set().union(*(self._switch_reach(c) for c in cores)) if cores else set()
        result = []
        for node, node_type in self.types.items():
            if not self.up(node) or node_type not in ("access_switch", "ap"):
                continue
            if node_type == "access_switch" and node not in reach:
                result.append(node)
            if node_type == "ap" and not any(n in reach for n in self.adj[node]):
                result.append(node)
        return sorted(result)

    # --- Layer 3 ---------------------------------------------------------

    def _iface_up(self, node: str, iface_name: str, iface) -> bool:
        if iface_name.startswith(("Vlan", "BVI")):
            return True  # SVIs stay up while the switch does
        peers = {self.owner[ip] for ip in self.owner if ip in iface.network and self.owner[ip] != node}
        return any(self.up(p) for p in peers)

    def _connected(self, node: str, addr) -> str | None:
        for name, iface in self.devices[node].addresses:
            if addr in iface.network and self._iface_up(node, name, iface):
                return name
        return None

    def _route_down(self, node: str, route, use_tracks: bool) -> str | None:
        """Why this route wouldn't be installed right now, or None if it would."""
        if route.next_hop == "dhcp":
            if not any(self.types.get(n) == "wan_uplink" and self.up(n) for n in self.adj[node]):
                return "its ISP circuit is down"
        else:
            hop = ipaddress.ip_address(route.next_hop)
            peer = self.owner.get(hop)
            if peer is None:
                return f"next hop {hop} doesn't exist"
            if not self.up(peer) or not self._connected(node, hop):
                return f"{peer} is down"
        if route.track is not None and use_tracks and not self._track_up(node, route.track):
            return f"track {route.track} is down"
        return None

    def _track_up(self, node: str, track: int) -> bool:
        key = (node, track)
        if key not in self._track_cache:
            dev = self.devices[node]
            sla = dev.tracks.get(track)
            target = dev.sla_targets.get(sla)
            ok = False
            if target is not None:
                # Send the probe from its real source address so the edge
                # router's NAT applies to it: an un-NATed probe gets no reply.
                source = dict(dev.addresses).get(dev.sla_sources.get(sla))
                src_net = ipaddress.ip_network(f"{source.ip}/32") if source else None
                dst_net = ipaddress.ip_network(f"{target}/32") if source else None
                ok, *_ = self._walk(node, None, target, dst_net, src_net, use_tracks=False)
            self._track_cache[key] = ok
        return self._track_cache[key]

    def _route(self, node: str, dst, use_tracks: bool = True):
        candidates = [r for r in self.devices[node].routes if dst in r.prefix]
        live, reasons = [], []
        for r in candidates:
            why = self._route_down(node, r, use_tracks)
            if why:
                reasons.append(why)
            else:
                live.append(r)
        if not live:
            return None, "; ".join(dict.fromkeys(reasons))
        best = max(r.prefix.prefixlen for r in live)
        live = [r for r in live if r.prefix.prefixlen == best]
        return min(live, key=lambda r: r.distance), None

    def _walk(self, node, ingress, dst_ip, dst_net, src_net, use_tracks=True):
        """Forward a packet from node. Returns (delivered, path, reason, exit_router).

        src_net None means a probe/return packet: ACLs and NAT aren't checked.
        """
        path = [node]
        for _ in range(MAX_HOPS):
            dev = self.devices[node]
            if src_net is not None and ingress:
                acl = dev.inbound_acl(ingress)
                if acl and acl in dev.acls:
                    verdict = acl_verdict(dev.acls[acl], src_net, dst_net)
                    if verdict != "permit":
                        how = "denied" if verdict == "deny" else "partly denied"
                        return False, path, f"{how} by ACL {acl} on {node} {ingress}", None
            if self._connected(node, dst_ip):
                return True, path, "delivered", None
            route, why = self._route(node, dst_ip, use_tracks)
            if route is None:
                return False, path, f"{node} has no working route to {dst_ip}" + (f" ({why})" if why else ""), None
            if route.next_hop == "dhcp":
                if src_net is not None:
                    nat = dev.acls.get("NAT-INSIDE")
                    if nat is None or acl_verdict(nat, src_net, ANY) != "permit":
                        return False, path, f"{node} doesn't NAT {src_net}", None
                isp = next(n for n in sorted(self.adj[node]) if self.types.get(n) == "wan_uplink" and self.up(n))
                return True, path + [isp, INTERNET], "delivered", node
            hop = ipaddress.ip_address(route.next_hop)
            nxt = self.owner[hop]
            if nxt in path:
                return False, path + [nxt], "routing loop", None
            ingress = next(name for name, iface in self.devices[nxt].addresses if iface.ip == hop)
            path.append(nxt)
            node = nxt
        return False, path, "too many hops", None

    def _gateway(self, vlan) -> tuple[str | None, str | None, str]:
        svi = f"Vlan{vlan.vlan_id}"
        cores = [
            n for n, dev in self.devices.items()
            if self.up(n) and any(name == svi for name, _ in dev.addresses)
        ]
        served = [(c, self._access_serving(c)) for c in cores]
        served = [(c, a) for c, a in served if a]
        if not served:
            if not cores:
                return None, None, f"no live core switch has a gateway for VLAN {vlan.vlan_id}"
            return None, None, "no live access switch connects users to a core switch"
        # HSRP: highest priority wins; ties go to the first core.
        core, access = max(served, key=lambda ca: self.devices[ca[0]].hsrp_priority(svi))
        return core, access, ""

    def flow(self, src: str, dst: str) -> FlowResult:
        vlan, src_net = self.by_name[src]
        core, access, why = self._gateway(vlan)
        if core is None:
            return FlowResult(source=src, destination=dst, allowed=False, path=[], reason=why)

        if dst == INTERNET:
            dst_ip, dst_net = INTERNET_PROBE, ipaddress.ip_network(f"{INTERNET_PROBE}/32")
        else:
            dst_vlan, dst_net = self.by_name[dst]
            dst_ip = dst_net.broadcast_address - 1  # any host; the gateway sits at the bottom

        ok, path, reason, exit_router = self._walk(core, f"Vlan{vlan.vlan_id}", dst_ip, dst_net, src_net)
        path = [access] + path
        if not ok:
            return FlowResult(source=src, destination=dst, allowed=False, path=path, reason=reason)

        if dst == INTERNET:
            # Replies come back through the router that NATed the request.
            back_ok, back_path, back_reason, _ = self._walk(
                exit_router, None, src_net.broadcast_address - 1, src_net, None,
            )
            if not back_ok or not self._access_serving(back_path[-1]):
                why = back_reason if not back_ok else f"{back_path[-1]} can't reach the {src} hosts"
                return FlowResult(source=src, destination=dst, allowed=False, path=path,
                                  reason=f"replies can't get back: {why}")
            return FlowResult(source=src, destination=dst, allowed=True, path=path,
                              reason=f"out via {exit_router}, replies return via {' -> '.join(back_path)}")

        if not self._access_serving(path[-1]):
            return FlowResult(source=src, destination=dst, allowed=False, path=path,
                              reason=f"{path[-1]} can't reach the {dst} hosts")

        # Replies need a way back too: the ACLs are stateless, so one that
        # drops them (the guest ACL does) means no connection can complete.
        dst_core, _, why = self._gateway(dst_vlan)
        if dst_core is None:
            return FlowResult(source=src, destination=dst, allowed=False, path=path,
                              reason=f"replies can't get back: {why}")
        back_ok, back_path, back_reason, _ = self._walk(
            dst_core, f"Vlan{dst_vlan.vlan_id}", src_net.broadcast_address - 1, src_net, dst_net,
        )
        if not back_ok or not self._access_serving(back_path[-1]):
            why = back_reason if not back_ok else f"{back_path[-1]} can't reach the {src} hosts"
            return FlowResult(source=src, destination=dst, allowed=False, path=path,
                              reason=f"replies can't get back: {why}")
        return FlowResult(source=src, destination=dst, allowed=True, path=path + [dst], reason="delivered")

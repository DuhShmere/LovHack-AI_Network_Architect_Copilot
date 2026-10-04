"""
The generator: NetworkSpec -> NetworkPlan.

Owner: Nyles. This is pure Python -- no API, no LLM calls -- so it's fully
testable standalone.
"""

import ipaddress
import math

from shared.schema import (
    NetworkSpec,
    NetworkPlan,
    RedundancyLevel,
    VLANAllocation,
    TopologyNode,
    TopologyLink,
)
from engine.taxonomy import (
    SEGMENT_CATALOG,
    CUSTOM_VLAN_START,
    CUSTOM_VLAN_STEP,
    USERS_PER_ACCESS_SWITCH,
    CLIENTS_PER_AP,
    normalize_segment,
    suggest_prefix_length,
)

DEFAULT_BASE_CIDR = "10.0.0.0/16"


class PlanGenerationError(ValueError):
    """The spec can't be turned into a plan (bad CIDR, doesn't fit, etc)."""


def generate_plan(spec: NetworkSpec) -> NetworkPlan:
    """Turn a structured spec into a concrete IP/VLAN + topology plan."""
    if spec.user_count < 1:
        raise PlanGenerationError(f"user_count must be at least 1, got {spec.user_count}")

    nodes, links = _build_topology(spec)
    segments = _resolve_segments(spec)
    vlans = _allocate_vlans(spec, segments, device_count=len(nodes))
    return NetworkPlan(spec=spec, vlans=vlans, nodes=nodes, links=links)


# ---------------------------------------------------------------------------
# Segments / VLANs
# ---------------------------------------------------------------------------

def _resolve_segments(spec: NetworkSpec) -> list[str]:
    """Deduped segment names: always staff + management, guest if asked for."""
    names = ["staff"]
    names += [normalize_segment(s) for s in spec.department_segments]
    if spec.needs_guest_wifi:
        names.append("guest")
    names.append("management")

    seen: set[str] = set()
    return [n for n in names if n and not (n in seen or seen.add(n))]


def _segment_host_count(name: str, spec: NetworkSpec, device_count: int) -> int:
    if name == "management":
        return device_count * 2  # room to grow the device count
    if name == "iot":
        return max(29, spec.user_count // 2)  # floor: a /27 incl. gateway
    if name == "servers":
        return 29
    # staff, guest, voip and custom departments: size for every user.
    return spec.user_count


def _allocate_vlans(
    spec: NetworkSpec, segments: list[str], device_count: int
) -> list[VLANAllocation]:
    try:
        base = ipaddress.ip_network(spec.preferred_base_cidr or DEFAULT_BASE_CIDR, strict=False)
    except ValueError as e:
        raise PlanGenerationError(f"Invalid preferred_base_cidr: {spec.preferred_base_cidr!r}") from e
    if base.version != 4:
        raise PlanGenerationError(f"Only IPv4 base CIDRs are supported, got {base}")

    # (vlan_id, name, purpose, prefix_len)
    wanted = []
    next_custom = CUSTOM_VLAN_START
    for name in segments:
        if name in SEGMENT_CATALOG:
            vlan_id, purpose = SEGMENT_CATALOG[name]
            if name == "guest" and spec.guest_wifi_isolated:
                purpose = "Isolated guest wifi"
        else:
            vlan_id, purpose = next_custom, f"{name.replace('-', ' ').title()} department"
            next_custom += CUSTOM_VLAN_STEP
        # +1 so the gateway address fits alongside the hosts.
        prefix = suggest_prefix_length(_segment_host_count(name, spec, device_count) + 1)
        wanted.append((vlan_id, name, purpose, prefix))

    subnets = _vlan_aligned_subnets(base, wanted) or _packed_subnets(base, wanted)
    return [
        VLANAllocation(vlan_id=vlan_id, name=name, subnet_cidr=str(subnets[vlan_id]), purpose=purpose)
        for vlan_id, name, purpose, _ in sorted(wanted)
    ]


def _vlan_aligned_subnets(base, wanted):
    """Readable layout: VLAN N lives at <base>.N.0 (e.g. VLAN 10 -> 10.0.10.0/26).

    Only possible when the base is /16 or bigger, every VLAN ID fits in an
    octet, and every subnet fits in a /24. Returns None otherwise.
    """
    if base.prefixlen > 16:
        return None
    if any(vlan_id > 255 or prefix < 24 for vlan_id, _, _, prefix in wanted):
        return None
    base_int = int(base.network_address)
    return {
        vlan_id: ipaddress.ip_network((base_int + vlan_id * 256, prefix))
        for vlan_id, _, _, prefix in wanted
    }


def _packed_subnets(base, wanted):
    """Largest-first contiguous packing; aligned blocks, so no overlap/gaps."""
    cursor = int(base.network_address)
    end = int(base.broadcast_address)
    result = {}
    for vlan_id, name, _, prefix in sorted(wanted, key=lambda w: (w[3], w[0])):
        if prefix < base.prefixlen:
            raise PlanGenerationError(
                f"Segment '{name}' needs a /{prefix}, larger than the base network {base}"
            )
        size = 2 ** (32 - prefix)
        cursor = -(-cursor // size) * size  # round up to block boundary
        if cursor + size - 1 > end:
            raise PlanGenerationError(
                f"Not enough address space in {base} for {len(wanted)} VLANs"
            )
        result[vlan_id] = ipaddress.ip_network((cursor, prefix))
        cursor += size
    return result


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def _build_topology(spec: NetworkSpec) -> tuple[list[TopologyNode], list[TopologyLink]]:
    dual_wan = spec.redundancy in (
        RedundancyLevel.dual_wan,
        RedundancyLevel.dual_wan_plus_switch_redundancy,
    )
    dual_core = spec.redundancy == RedundancyLevel.dual_wan_plus_switch_redundancy

    nodes: list[TopologyNode] = []
    links: list[TopologyLink] = []

    def node(node_id, node_type, label):
        nodes.append(TopologyNode(node_id=node_id, node_type=node_type, label=label))

    def link(source_id, target_id, link_type):
        links.append(TopologyLink(source_id=source_id, target_id=target_id, link_type=link_type))

    # WAN edge
    node("isp-a", "wan_uplink", "ISP Circuit A")
    node("router1", "router", "Edge Router 1")
    link("isp-a", "router1", "wan")
    if dual_wan:
        node("isp-b", "wan_uplink", "ISP Circuit B")
        node("router2", "router", "Edge Router 2 (redundant)")
        link("isp-b", "router2", "redundant_wan")

    # Firewalls: full redundancy pairs them too (active/standby, sharing
    # state over a failover link), so the firewall isn't the one device
    # whose loss takes everyone offline.
    routers = ["router1", "router2"] if dual_wan else ["router1"]
    firewalls = ["firewall1", "firewall2"] if dual_core else ["firewall1"]
    for i, fw_id in enumerate(firewalls, start=1):
        node(fw_id, "firewall", f"Perimeter Firewall {i}" if dual_core else "Perimeter Firewall")
        for router_id in routers:
            link(router_id, fw_id, "trunk")
    if dual_core:
        link("firewall1", "firewall2", "failover")

    # Core
    cores = ["core1", "core2"] if dual_core else ["core1"]
    for i, core_id in enumerate(cores, start=1):
        node(core_id, "core_switch", f"Core Switch {i}" if dual_core else "Core Switch")
        for fw_id in firewalls:
            link(fw_id, core_id, "trunk")
    if dual_core:
        link("core1", "core2", "trunk")

    # Access layer: every access switch uplinks to every core switch.
    access_count = math.ceil(spec.user_count / USERS_PER_ACCESS_SWITCH)
    access_ids = [f"access{i}" for i in range(1, access_count + 1)]
    for i, access_id in enumerate(access_ids, start=1):
        node(access_id, "access_switch", f"Access Switch {i}")
        for core_id in cores:
            link(core_id, access_id, "trunk")

    # APs, spread round-robin across access switches.
    ap_count = math.ceil(spec.user_count / CLIENTS_PER_AP)
    for i in range(1, ap_count + 1):
        ap_id = f"ap{i}"
        node(ap_id, "ap", f"Wireless AP {i}")
        link(access_ids[(i - 1) % access_count], ap_id, "access")

    return nodes, links

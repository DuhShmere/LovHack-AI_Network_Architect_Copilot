"""
The generator: NetworkSpec -> NetworkPlan.

Owner: Nyles. This is pure Python -- no API, no LLM calls -- so it's fully
testable standalone (Day 2 task: hand Samir a working generate_plan()
function by end of Day 3).
"""

import ipaddress
import math

from shared.schema import NetworkSpec, NetworkPlan, VLANAllocation, TopologyNode, TopologyLink
from engine.taxonomy import suggest_prefix_length


def generate_plan(spec: NetworkSpec) -> NetworkPlan:
    """Turn a structured spec into deterministic VLAN, IP, and topology plans."""
    if spec.user_count < 1:
        raise ValueError("user_count must be at least 1")

    segments = list(dict.fromkeys(
        segment.strip().lower()
        for segment in spec.department_segments
        if segment.strip()
    ))
    if spec.needs_guest_wifi and "guest" not in segments:
        segments.append("guest")
    if not segments:
        segments = ["staff"]

    base_cidr = spec.preferred_base_cidr or "10.0.0.0/8"
    try:
        base_network = ipaddress.IPv4Network(base_cidr, strict=False)
    except ValueError as exc:
        raise ValueError(f"preferred_base_cidr must be a valid IPv4 network: {base_cidr}") from exc

    prefix_length = suggest_prefix_length(spec.user_count + 1)
    if prefix_length < base_network.prefixlen:
        raise ValueError(
            f"preferred_base_cidr {base_network} is too small for /{prefix_length} VLAN subnets"
        )
    subnet_candidates = (
        base_network.subnets(new_prefix=prefix_length)
        if prefix_length > base_network.prefixlen
        else iter((base_network,))
    )

    vlans = []
    for index, segment in enumerate(segments):
        if index >= 409:
            raise ValueError("too many segments to assign VLAN IDs from 10 through 4090")
        try:
            subnet = next(subnet_candidates)
        except StopIteration as exc:
            raise ValueError(
                f"preferred_base_cidr {base_network} cannot fit {len(segments)} VLAN subnets"
            ) from exc
        purpose = f"{segment} network"
        if segment == "guest" and spec.guest_wifi_isolated:
            purpose = "isolated guest network"
        vlans.append(VLANAllocation(
            vlan_id=(index + 1) * 10,
            name=segment,
            subnet_cidr=str(subnet),
            purpose=purpose,
        ))

    switch_count = max(1, math.ceil(spec.user_count / 48))
    nodes = [
        TopologyNode(node_id="router-primary", node_type="router", label="Primary WAN router"),
        TopologyNode(node_id="firewall", node_type="firewall", label="Firewall"),
        TopologyNode(node_id="core-switch-1", node_type="core_switch", label="Core switch 1"),
    ]
    links = [
        TopologyLink(source_id="router-primary", target_id="firewall", link_type="wan"),
        TopologyLink(source_id="firewall", target_id="core-switch-1", link_type="trunk"),
    ]

    if spec.redundancy in {
        "dual_wan",
        "dual_wan_plus_switch_redundancy",
    }:
        nodes.append(TopologyNode(
            node_id="router-secondary", node_type="router", label="Secondary WAN router"
        ))
        links.append(TopologyLink(
            source_id="router-secondary", target_id="firewall", link_type="redundant_wan"
        ))

    core_switch_ids = ["core-switch-1"]
    if spec.redundancy == "dual_wan_plus_switch_redundancy":
        nodes.append(TopologyNode(
            node_id="core-switch-2", node_type="core_switch", label="Core switch 2"
        ))
        links.append(TopologyLink(
            source_id="firewall", target_id="core-switch-2", link_type="trunk"
        ))
        core_switch_ids.append("core-switch-2")

    for index in range(switch_count):
        switch_id = f"access-switch-{index + 1}"
        nodes.append(TopologyNode(
            node_id=switch_id,
            node_type="access_switch",
            label=f"Access switch {index + 1}",
        ))
        for core_switch_id in core_switch_ids:
            links.append(TopologyLink(
                source_id=core_switch_id,
                target_id=switch_id,
                link_type="trunk",
            ))

    if spec.needs_guest_wifi:
        nodes.append(TopologyNode(node_id="ap-1", node_type="ap", label="Wireless access point"))
        links.append(TopologyLink(
            source_id="access-switch-1", target_id="ap-1", link_type="access"
        ))

    return NetworkPlan(spec=spec, vlans=vlans, nodes=nodes, links=links)

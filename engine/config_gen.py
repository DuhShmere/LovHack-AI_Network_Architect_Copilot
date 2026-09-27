"""
Cisco-style config generator: NetworkPlan -> list[DeviceConfig].

Owner: Nyles. Day 5 task. Only run this on a plan that has already
passed validate_plan() -- don't generate configs for an invalid design.
"""

import re

from shared.schema import NetworkPlan, DeviceConfig


def _hostname(label: str) -> str:
    hostname = re.sub(r"[^A-Za-z0-9-]+", "-", label).strip("-").lower()[:63]
    if not hostname:
        hostname = "network-device"
    if hostname[0].isdigit():
        hostname = f"device-{hostname}"[:63]
    return hostname


def _vlan_name(name: str, vlan_id: int) -> str:
    vlan_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-_")[:32]
    return vlan_name or f"VLAN{vlan_id}"


def generate_configs(plan: NetworkPlan) -> list[DeviceConfig]:
    """Generate illustrative IOS-style configs without inventing missing IP data."""
    nodes_by_id = {node.node_id: node for node in plan.nodes}
    vlan_ids = ",".join(str(vlan.vlan_id) for vlan in plan.vlans)
    guest_vlan = next(
        (vlan.vlan_id for vlan in plan.vlans if vlan.name.casefold() == "guest"),
        None,
    )
    configs = []

    for node in plan.nodes:
        lines = [
            f"hostname {_hostname(node.label)}",
            "! Illustrative only: verify interface numbering and platform syntax before deployment.",
        ]
        if node.node_type in {"core_switch", "access_switch"}:
            for vlan in plan.vlans:
                lines.extend([
                    "!",
                    f"vlan {vlan.vlan_id}",
                    f" name {_vlan_name(vlan.name, vlan.vlan_id)}",
                ])

        adjacent_links = [
            link for link in plan.links
            if node.node_id in {link.source_id, link.target_id}
        ]
        for port_number, link in enumerate(adjacent_links, start=1):
            peer_id = link.target_id if link.source_id == node.node_id else link.source_id
            peer = nodes_by_id.get(peer_id)
            peer_label = peer.label if peer else peer_id
            safe_peer_label = re.sub(r"[^A-Za-z0-9 ._-]", " ", peer_label)[:70].strip()

            if node.node_type in {"core_switch", "access_switch"}:
                interface_name = f"GigabitEthernet1/0/{port_number}"
            else:
                interface_name = f"GigabitEthernet0/{port_number}"
            lines.extend([
                "!",
                f"interface {interface_name}",
                f" description {link.link_type} link to {safe_peer_label or peer_id}",
            ])

            if node.node_type in {"core_switch", "access_switch"}:
                if link.link_type == "trunk":
                    lines.append(" switchport mode trunk")
                    if vlan_ids:
                        lines.append(f" switchport trunk allowed vlan {vlan_ids}")
                elif link.link_type == "access" and peer and peer.node_type == "ap":
                    lines.append(" switchport mode access")
                    if guest_vlan is not None:
                        lines.append(f" switchport access vlan {guest_vlan}")
                lines.append(" no shutdown")
            elif node.node_type in {"router", "firewall"}:
                lines.append(" no shutdown")

        if node.node_type == "ap":
            lines.append("! SSID and wireless security settings are not represented in NetworkPlan.")
        elif node.node_type in {"router", "firewall"}:
            lines.append("! IP addressing, routing, and firewall policy are not represented in NetworkPlan.")

        configs.append(DeviceConfig(
            node_id=node.node_id,
            config_text="\n".join(lines) + "\n",
        ))

    return configs

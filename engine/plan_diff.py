"""
Plan diff: what changed between two NetworkPlans, in plain sentences.

Owner: Nyles. Used when a design is refined ("make it 150 users") so the
dashboard can say exactly what the change did to the network.
"""

from collections import Counter

from shared.schema import NetworkPlan

SPEC_FIELDS = {
    "org_name": "Organization",
    "user_count": "Users",
    "needs_guest_wifi": "Guest wifi",
    "guest_wifi_isolated": "Guest isolation",
    "redundancy": "Redundancy",
    "preferred_base_cidr": "Base network",
}

DEVICE_NAMES = {  # plural, as they're reported as counts
    "wan_uplink": "ISP circuits",
    "router": "routers",
    "firewall": "firewalls",
    "core_switch": "core switches",
    "access_switch": "access switches",
    "ap": "access points",
}


def diff_plans(old: NetworkPlan, new: NetworkPlan) -> list[str]:
    changes = []

    for field, label in SPEC_FIELDS.items():
        a, b = getattr(old.spec, field), getattr(new.spec, field)
        if a != b:
            changes.append(f"{label}: {_show(a)} -> {_show(b)}")

    old_vlans = {v.name: v for v in old.vlans}
    new_vlans = {v.name: v for v in new.vlans}
    for name in sorted(new_vlans.keys() - old_vlans.keys()):
        v = new_vlans[name]
        changes.append(f"Added VLAN {v.vlan_id} '{name}' ({v.subnet_cidr})")
    for name in sorted(old_vlans.keys() - new_vlans.keys()):
        v = old_vlans[name]
        changes.append(f"Removed VLAN {v.vlan_id} '{name}' ({v.subnet_cidr})")
    for name in sorted(old_vlans.keys() & new_vlans.keys()):
        a, b = old_vlans[name], new_vlans[name]
        if a.subnet_cidr != b.subnet_cidr:
            changes.append(f"VLAN {b.vlan_id} '{name}' readdressed: {a.subnet_cidr} -> {b.subnet_cidr}")

    old_counts = Counter(n.node_type for n in old.nodes)
    new_counts = Counter(n.node_type for n in new.nodes)
    for node_type in [t for t in DEVICE_NAMES if t in old_counts or t in new_counts] + sorted(
        (old_counts.keys() | new_counts.keys()) - DEVICE_NAMES.keys()
    ):
        a, b = old_counts[node_type], new_counts[node_type]
        if a != b:
            noun = DEVICE_NAMES.get(node_type, node_type)
            changes.append(f"{noun[0].upper()}{noun[1:]}: {a} -> {b}")

    return changes or ["No change to the design."]


def _show(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(getattr(value, "value", value))

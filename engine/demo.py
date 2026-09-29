"""
Demo sabotage: take a valid NetworkPlan and break it in one named,
realistic way, so the validator can be shown catching it live.

Owner: Nyles. Pure engine/ -- the API/dashboard only needs:
  - list_sabotages(plan): which breakages apply to this plan (for buttons)
  - sabotage_plan(plan, key): a broken *copy* of the plan + what changed

Each sabotage targets exactly one validator check (target_check), so the
demo can say "we broke X, and check Y caught it".
"""

import ipaddress
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from shared.schema import NetworkPlan, RedundancyLevel, TopologyLink
from engine.taxonomy import normalize_segment


class SabotageError(ValueError):
    """Unknown sabotage key, or one that doesn't apply to this plan."""


class SabotageInfo(BaseModel):
    key: str
    label: str
    target_check: str


@dataclass(frozen=True)
class _Sabotage:
    label: str
    target_check: str
    applies: Callable[[NetworkPlan], bool]
    apply: Callable[[NetworkPlan], str]  # mutates the copy, returns what changed


def list_sabotages(plan: NetworkPlan) -> list[SabotageInfo]:
    return [
        SabotageInfo(key=key, label=s.label, target_check=s.target_check)
        for key, s in _SABOTAGES.items()
        if s.applies(plan)
    ]


def sabotage_plan(plan: NetworkPlan, key: str) -> tuple[NetworkPlan, str]:
    """Return (broken copy of plan, one-sentence description of the change)."""
    sabotage = _SABOTAGES.get(key)
    if sabotage is None:
        raise SabotageError(f"Unknown sabotage '{key}'. Options: {', '.join(_SABOTAGES)}")
    if not sabotage.applies(plan):
        raise SabotageError(f"Sabotage '{key}' doesn't apply to this plan")
    broken = plan.model_copy(deep=True)
    return broken, sabotage.apply(broken)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vlan(plan, name):
    return next((v for v in plan.vlans if v.name == name), None)


def _has(plan, name):
    return _vlan(plan, name) is not None


def _requested_segments(plan):
    spec = plan.spec
    names = [normalize_segment(s) for s in spec.department_segments]
    if spec.needs_guest_wifi:
        names.append("guest")
    return [n for n in names if n and _has(plan, n)]


def _node_ids(plan, node_type):
    return [n.node_id for n in plan.nodes if n.node_type == node_type]


# ---------------------------------------------------------------------------
# The sabotages -- one per validator check
# ---------------------------------------------------------------------------

def _overlapping_subnets(plan):
    staff, mgmt = _vlan(plan, "staff"), _vlan(plan, "management")
    old = mgmt.subnet_cidr
    mgmt.subnet_cidr = staff.subnet_cidr
    return (
        f"Moved the management VLAN {mgmt.vlan_id} from {old} onto {staff.subnet_cidr}, "
        f"the same subnet as staff VLAN {staff.vlan_id}."
    )


def _public_subnet(plan):
    staff = _vlan(plan, "staff")
    old = ipaddress.ip_network(staff.subnet_cidr)
    new = ipaddress.ip_network(("8.8.0.0", old.prefixlen), strict=False)
    staff.subnet_cidr = str(new)
    return f"Changed the staff subnet from {old} to {staff.subnet_cidr}, a public address range."


def _undersized_subnet(plan):
    staff = _vlan(plan, "staff")
    users = plan.spec.user_count
    net = ipaddress.ip_network(staff.subnet_cidr)
    prefix = net.prefixlen
    # Shrink until it no longer fits every user plus the gateway -- usually
    # just one size too small, the classic mistake.
    while prefix < 32 and 2 ** (32 - prefix) - 3 >= users:
        prefix += 1
    staff.subnet_cidr = str(ipaddress.ip_network((net.network_address, prefix)))
    return f"Shrank the staff subnet from {net} to {staff.subnet_cidr} for {users} users."


def _duplicate_vlan_id(plan):
    staff, mgmt = _vlan(plan, "staff"), _vlan(plan, "management")
    old = mgmt.vlan_id
    mgmt.vlan_id = staff.vlan_id
    return f"Renumbered the management VLAN from {old} to {staff.vlan_id}, the same ID as staff."


def _dispensable_segment(plan):
    # Only a secondary (non-staff, non-guest) segment can be dropped cleanly:
    # dropping staff also trips subnet_capacity, and dropping guest also
    # trips guest_isolation (if isolation was requested).
    requested = _requested_segments(plan)
    return next((n for n in requested if n not in ("staff", "guest")), None)


def _missing_segment(plan):
    victim = _dispensable_segment(plan)
    dropped = _vlan(plan, victim)
    plan.vlans.remove(dropped)
    return f"Deleted the {victim} VLAN {dropped.vlan_id} that the requirements asked for."


def _orphaned_switch(plan):
    access = _node_ids(plan, "access_switch")[-1]
    upstream = set(_node_ids(plan, "core_switch"))
    before = len(plan.links)
    plan.links = [
        l for l in plan.links
        if not ({l.source_id, l.target_id} & {access} and {l.source_id, l.target_id} & upstream)
    ]
    removed = before - len(plan.links)
    return f"Unplugged {access} from the core ({removed} uplink{'s' if removed != 1 else ''} removed)."


def _missing_backup_wan(plan):
    uplinks = [
        l for l in plan.links if l.link_type == "redundant_wan"
    ]
    backup_isp = next(
        (end for l in uplinks for end in (l.source_id, l.target_id) if end.startswith("isp")),
        None,
    )
    plan.links = [l for l in plan.links if l not in uplinks]
    plan.nodes = [n for n in plan.nodes if n.node_id != backup_isp]
    return f"Removed the backup ISP circuit {backup_isp}, even though dual WAN was required."


def _firewall_bypass(plan):
    router, core = _node_ids(plan, "router")[0], _node_ids(plan, "core_switch")[0]
    plan.links.append(TopologyLink(source_id=router, target_id=core, link_type="trunk"))
    return f"Cabled {router} directly to {core}, giving LAN traffic a path around the firewall."


_SABOTAGES: dict[str, _Sabotage] = {
    "overlapping_subnets": _Sabotage(
        "Two VLANs share a subnet", "no_subnet_overlap",
        lambda p: _has(p, "staff") and _has(p, "management"), _overlapping_subnets,
    ),
    "public_subnet": _Sabotage(
        "Use a public IP range internally", "valid_ranges",
        lambda p: _has(p, "staff"), _public_subnet,
    ),
    "undersized_subnet": _Sabotage(
        "Staff subnet one size too small", "subnet_capacity",
        lambda p: _has(p, "staff"), _undersized_subnet,
    ),
    "duplicate_vlan_id": _Sabotage(
        "Two VLANs share an ID", "valid_vlan_ids",
        lambda p: _has(p, "staff") and _has(p, "management"), _duplicate_vlan_id,
    ),
    "missing_segment": _Sabotage(
        "Forget a requested segment", "required_segments_present",
        lambda p: _dispensable_segment(p) is not None, _missing_segment,
    ),
    "orphaned_switch": _Sabotage(
        "Unplug an access switch", "topology_connected",
        lambda p: bool(_node_ids(p, "access_switch")) and bool(_node_ids(p, "core_switch")),
        _orphaned_switch,
    ),
    "missing_backup_wan": _Sabotage(
        "Drop the backup ISP", "redundancy_present",
        lambda p: p.spec.redundancy != RedundancyLevel.none
        and any(l.link_type == "redundant_wan" for l in p.links),
        _missing_backup_wan,
    ),
    "firewall_bypass": _Sabotage(
        "Cable around the firewall", "guest_isolation",
        lambda p: p.spec.guest_wifi_isolated
        and bool(_node_ids(p, "router")) and bool(_node_ids(p, "core_switch")),
        _firewall_bypass,
    ),
}

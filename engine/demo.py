"""
Demo sabotage: take a valid NetworkPlan and break it in one named,
realistic way, so the validator can be shown catching it live.

Owner: Nyles. Pure engine/ -- the API/dashboard only needs:
  - list_sabotages(plan): which breakages apply to this plan (for buttons)
  - sabotage_plan(plan, key): a broken *copy* of the plan + what changed

Each sabotage targets exactly one validator check (target_check), so the
demo can say "we broke X, and check Y caught it".

Design sabotages break the plan itself. Config sabotages leave the plan
sound and instead tamper with the configs generated from it -- the kind
of slip a hand edit makes -- so the config audit has to catch them.
break_design() runs either kind and re-validates.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from shared.schema import DeviceConfig, NetworkPlan, RedundancyLevel, TopologyLink, ValidationReport
from engine.taxonomy import normalize_segment
from engine.config_gen import generate_configs
from engine.validator import node_ids_of_type, validate_deployment, validate_plan


class SabotageError(ValueError):
    """Unknown sabotage key, or one that doesn't apply to this plan."""


class SabotageInfo(BaseModel):
    key: str
    label: str
    target_check: str
    kind: str = "design"  # "design" (breaks the plan) or "config" (tampers with its configs)


@dataclass(frozen=True)
class _Sabotage:
    label: str
    target_check: str
    applies: Callable[[NetworkPlan], bool]
    apply: Callable[[NetworkPlan], str]  # mutates the copy, returns what changed


@dataclass(frozen=True)
class _ConfigSabotage:
    label: str
    target_check: str
    applies: Callable[[NetworkPlan], bool]
    apply: Callable[[dict[str, str], NetworkPlan], str]  # edits node_id -> config text, returns what changed


def list_sabotages(plan: NetworkPlan) -> list[SabotageInfo]:
    return [
        SabotageInfo(key=key, label=s.label, target_check=s.target_check)
        for key, s in _SABOTAGES.items()
        if s.applies(plan)
    ] + [
        SabotageInfo(key=key, label=s.label, target_check=s.target_check, kind="config")
        for key, s in _CONFIG_SABOTAGES.items()
        if s.applies(plan)
    ]


def break_design(plan: NetworkPlan, key: str) -> tuple[NetworkPlan, str, ValidationReport]:
    """Run either kind of sabotage. Returns (the plan as broken -- unchanged
    for a config sabotage, what changed, the validation report)."""
    sabotage = _CONFIG_SABOTAGES.get(key)
    if sabotage is None:
        broken, what = sabotage_plan(plan, key)
        return broken, what, validate_plan(broken)
    if not sabotage.applies(plan):
        raise SabotageError(f"Sabotage '{key}' doesn't apply to this plan")
    texts = {c.node_id: c.config_text for c in generate_configs(plan)}
    what = sabotage.apply(texts, plan)
    configs = [DeviceConfig(node_id=k, config_text=v) for k, v in texts.items()]
    return plan, what, validate_deployment(plan, configs)


def sabotage_plan(plan: NetworkPlan, key: str) -> tuple[NetworkPlan, str]:
    """Return (broken copy of plan, one-sentence description of the change)."""
    sabotage = _SABOTAGES.get(key)
    if sabotage is None:
        options = ", ".join([*_SABOTAGES, *_CONFIG_SABOTAGES])
        raise SabotageError(f"Unknown sabotage '{key}'. Options: {options}")
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
    access = node_ids_of_type(plan, "access_switch")[-1]
    upstream = set(node_ids_of_type(plan, "core_switch"))
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
    router, core = node_ids_of_type(plan, "router")[0], node_ids_of_type(plan, "core_switch")[0]
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
        lambda p: bool(node_ids_of_type(p, "access_switch")) and bool(node_ids_of_type(p, "core_switch")),
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
        and bool(node_ids_of_type(p, "router")) and bool(node_ids_of_type(p, "core_switch")),
        _firewall_bypass,
    ),
}


# ---------------------------------------------------------------------------
# Config sabotages -- one per config audit check
# ---------------------------------------------------------------------------

def _edit_block(text: str, header: str, edit: Callable[[str], str | None]) -> str:
    """Rewrite the body lines of the config block starting at header:
    edit(line) returns the replacement line, or None to delete it."""
    out, inside = [], False
    for line in text.split("\n"):
        if not line.startswith(" "):
            inside = line == header
        elif inside:
            line = edit(line)
            if line is None:
                continue
        out.append(line)
    return "\n".join(out)


def _block_value(text: str, header: str, pattern: str) -> str:
    """The first group of pattern matched against a body line of header's block."""
    found = []
    _edit_block(text, header, lambda l: found.append(m.group(1)) or l if (m := re.fullmatch(pattern, l)) else l)
    return found[0]


def _strip_guest_acl(texts, plan):
    core = node_ids_of_type(plan, "core_switch")[0]
    guest = _vlan(plan, "guest")
    texts[core] = _edit_block(
        texts[core], f"interface Vlan{guest.vlan_id}",
        lambda l: None if l == " ip access-group GUEST-ISOLATION in" else l,
    )
    return (
        f"Deleted 'ip access-group GUEST-ISOLATION in' from {core}'s guest VLAN {guest.vlan_id} "
        f"interface: the isolation ACL still exists, but nothing applies it."
    )


def _wrong_dhcp_gateway(texts, plan):
    core = node_ids_of_type(plan, "core_switch")[0]
    old = _block_value(texts[core], "ip dhcp pool STAFF", r" default-router (\S+)")
    new = ipaddress.ip_address(old) + 5
    texts[core] = _edit_block(
        texts[core], "ip dhcp pool STAFF",
        lambda l: f" default-router {new}" if l == f" default-router {old}" else l,
    )
    return f"Pointed {core}'s staff DHCP pool at gateway {new} instead of {old}: every staff device would get a dead gateway."


def _duplicate_ip(texts, plan):
    access = node_ids_of_type(plan, "access_switch")[0]
    svi = f"interface Vlan{_vlan(plan, 'management').vlan_id}"
    own, mask = _block_value(texts[access], svi, r" ip address (\S+ \S+)").split()
    gateway = re.search(r"^ip default-gateway (\S+)$", texts[access], re.M).group(1)
    texts[access] = _edit_block(
        texts[access], svi,
        lambda l: f" ip address {gateway} {mask}" if l == f" ip address {own} {mask}" else l,
    )
    return f"Gave {access}'s management interface {gateway} instead of {own}: that's the management gateway's address."


def _drop_return_route(texts, plan):
    firewall = node_ids_of_type(plan, "firewall")[0]
    staff = ipaddress.ip_network(_vlan(plan, "staff").subnet_cidr)
    prefix = f"ip route {staff.network_address} {staff.netmask} "
    texts[firewall] = "\n".join(l for l in texts[firewall].split("\n") if not l.startswith(prefix))
    return (
        f"Deleted {firewall}'s route back to the staff subnet {staff}: replies to staff fall through "
        f"to the default route and loop back out."
    )


_CONFIG_SABOTAGES: dict[str, _ConfigSabotage] = {
    "strip_guest_acl": _ConfigSabotage(
        "Forget to apply the guest ACL", "guest_isolation_enforced",
        lambda p: p.spec.guest_wifi_isolated and _has(p, "guest") and bool(node_ids_of_type(p, "core_switch")),
        _strip_guest_acl,
    ),
    "wrong_dhcp_gateway": _ConfigSabotage(
        "Typo in the DHCP gateway", "gateways_consistent",
        lambda p: _has(p, "staff") and bool(node_ids_of_type(p, "core_switch")),
        _wrong_dhcp_gateway,
    ),
    "duplicate_ip": _ConfigSabotage(
        "Reuse an IP address", "no_ip_conflicts",
        lambda p: _has(p, "management") and bool(node_ids_of_type(p, "access_switch")),
        _duplicate_ip,
    ),
    "drop_return_route": _ConfigSabotage(
        "Delete a firewall return route", "routing_complete",
        lambda p: _has(p, "staff") and bool(node_ids_of_type(p, "firewall")),
        _drop_return_route,
    ),
}

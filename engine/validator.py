"""
The validator: NetworkPlan -> ValidationReport.

Owner: Nyles. This is the "proof it's real" demo moment -- it runs a fixed
set of concrete checks and returns pass/fail with reasons, not another
LLM call.

Once the design checks pass, the configs generated from it are audited
too (engine/config_audit.py), so the report covers what actually ships.

Every check takes the plan and returns one ValidationCheck. Checks must
never raise on a malformed plan -- a broken plan should come back as a
failed report, not a 500.
"""

import ipaddress
import itertools
from collections import defaultdict, deque

from shared.schema import DeviceConfig, NetworkPlan, ValidationReport, ValidationCheck, RedundancyLevel, VLANAllocation
from engine.taxonomy import normalize_segment
from engine.config_gen import generate_configs
from engine.config_audit import audit_configs

RFC1918 = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]
RESERVED_VLAN_IDS = {1, 1002, 1003, 1004, 1005}  # default + legacy FDDI/Token Ring


def validate_plan(plan: NetworkPlan) -> ValidationReport:
    # Only a sound design gets configs, so only a sound design gets its
    # configs audited -- and a broken one keeps exactly the checks it failed.
    checks = _design_checks(plan)
    if all(c.passed for c in checks):
        checks += audit_generated_configs(plan)
    return ValidationReport(overall_pass=all(c.passed for c in checks), checks=checks)


def validate_deployment(plan: NetworkPlan, configs: list[DeviceConfig]) -> ValidationReport:
    """Like validate_plan, but audits the configs given (e.g. hand-edited
    ones) instead of freshly generated ones."""
    checks = _design_checks(plan)
    if all(c.passed for c in checks):
        checks += audit_configs(plan, configs)
    return ValidationReport(overall_pass=all(c.passed for c in checks), checks=checks)


def _design_checks(plan: NetworkPlan) -> list[ValidationCheck]:
    return [
        check_valid_ranges(plan),
        check_no_subnet_overlap(plan),
        check_valid_vlan_ids(plan),
        check_required_segments_present(plan),
        check_subnet_capacity(plan),
        check_topology_connected(plan),
        check_redundancy_present(plan),
        check_guest_isolation(plan),
    ]


def audit_generated_configs(plan: NetworkPlan) -> list[ValidationCheck]:
    try:
        configs = generate_configs(plan)
    except Exception as e:  # never 500 on a plan the generator can't render
        return [_check("configs_generated", False, f"Config generation failed: {e}")]
    return audit_configs(plan, configs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check(name: str, passed: bool, detail: str) -> ValidationCheck:
    return ValidationCheck(check_name=name, passed=passed, detail=detail)


def _parsed_subnets(plan: NetworkPlan) -> list[tuple[VLANAllocation, ipaddress.IPv4Network]]:
    """(vlan, network) pairs, skipping anything that doesn't parse (valid_ranges reports those).

    A list rather than a dict keyed by vlan_id, so duplicate IDs (which
    valid_vlan_ids reports) can't hide one VLAN's subnet from the other checks.
    """
    result = []
    for v in plan.vlans:
        try:
            result.append((v, ipaddress.ip_network(v.subnet_cidr)))
        except ValueError:
            pass
    return result


def _adjacency(plan: NetworkPlan) -> dict[str, set[str]]:
    adj = defaultdict(set)
    for link in plan.links:
        adj[link.source_id].add(link.target_id)
        adj[link.target_id].add(link.source_id)
    return adj


def _reachable(adj: dict[str, set[str]], start: set[str], blocked: set[str] = frozenset()) -> set[str]:
    seen = set(start) - blocked
    queue = deque(seen)
    while queue:
        for nxt in adj[queue.popleft()]:
            if nxt not in seen and nxt not in blocked:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def node_ids_of_type(plan: NetworkPlan, node_type: str) -> list[str]:
    return [n.node_id for n in plan.nodes if n.node_type == node_type]


# ---------------------------------------------------------------------------
# Addressing checks
# ---------------------------------------------------------------------------

def check_valid_ranges(plan: NetworkPlan) -> ValidationCheck:
    problems = []
    for v in plan.vlans:
        try:
            net = ipaddress.ip_network(v.subnet_cidr)
        except ValueError:
            problems.append(f"VLAN {v.vlan_id} '{v.subnet_cidr}' is not a valid network (host bits set?)")
            continue
        if not any(net.version == r.version and net.subnet_of(r) for r in RFC1918):
            problems.append(f"VLAN {v.vlan_id} {net} is outside RFC1918 private space")
    if problems:
        return _check("valid_ranges", False, "; ".join(problems))
    return _check(
        "valid_ranges", True,
        f"All {len(plan.vlans)} subnets are valid and within RFC1918 private address space.",
    )


def check_no_subnet_overlap(plan: NetworkPlan) -> ValidationCheck:
    subnets = _parsed_subnets(plan)
    clashes = [
        f"VLAN {a.vlan_id} ({a_net}) overlaps VLAN {b.vlan_id} ({b_net})"
        for (a, a_net), (b, b_net) in itertools.combinations(subnets, 2)
        if a_net.overlaps(b_net)
    ]
    if clashes:
        return _check("no_subnet_overlap", False, "; ".join(clashes))
    return _check("no_subnet_overlap", True, f"All {len(subnets)} VLAN subnets are disjoint.")


def check_valid_vlan_ids(plan: NetworkPlan) -> ValidationCheck:
    ids = [v.vlan_id for v in plan.vlans]
    problems = []
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate VLAN IDs {dupes}")
    bad = sorted({i for i in ids if not 1 <= i <= 4094 or i in RESERVED_VLAN_IDS})
    if bad:
        problems.append(f"VLAN IDs {bad} are out of range or reserved (1, 1002-1005)")
    if problems:
        return _check("valid_vlan_ids", False, "; ".join(problems))
    return _check(
        "valid_vlan_ids", True,
        f"VLAN IDs {sorted(ids)} are unique and avoid reserved IDs 1 and 1002-1005.",
    )


def check_required_segments_present(plan: NetworkPlan) -> ValidationCheck:
    spec = plan.spec
    required = {normalize_segment(s) for s in spec.department_segments} - {""}
    if spec.needs_guest_wifi:
        required.add("guest")
    have = {v.name for v in plan.vlans}
    missing = sorted(required - have)
    if missing:
        return _check(
            "required_segments_present", False,
            f"Requested segments with no VLAN: {', '.join(missing)}.",
        )
    return _check(
        "required_segments_present", True,
        f"Every requested segment has its own VLAN ({', '.join(sorted(required)) or 'none requested'}).",
    )


def check_subnet_capacity(plan: NetworkPlan) -> ValidationCheck:
    """User-facing VLANs must have room for every user plus a gateway."""
    users = plan.spec.user_count
    problems = []
    checked = []
    for v, net in _parsed_subnets(plan):
        if v.name not in ("staff", "guest"):
            continue
        usable = net.num_addresses - 2 - 1  # network, broadcast, gateway
        checked.append(f"{v.name} {net} ({usable} hosts)")
        if usable < users:
            problems.append(f"{v.name} {net} has {usable} usable hosts for {users} users")
    if problems:
        return _check("subnet_capacity", False, "; ".join(problems))
    if not checked:
        return _check("subnet_capacity", False, "No staff VLAN found to hold users.")
    return _check(
        "subnet_capacity", True,
        f"Room for all {users} users after reserving a gateway: {', '.join(checked)}.",
    )


# ---------------------------------------------------------------------------
# Topology checks
# ---------------------------------------------------------------------------

def check_topology_connected(plan: NetworkPlan) -> ValidationCheck:
    ids = {n.node_id for n in plan.nodes}
    dangling = sorted(
        {l.source_id for l in plan.links if l.source_id not in ids}
        | {l.target_id for l in plan.links if l.target_id not in ids}
    )
    if dangling:
        return _check("topology_connected", False, f"Links reference unknown nodes: {', '.join(dangling)}.")

    uplinks = set(node_ids_of_type(plan, "wan_uplink"))
    if not uplinks:
        return _check("topology_connected", False, "No WAN uplink -- the network has no path to the internet.")

    stranded = sorted(ids - _reachable(_adjacency(plan), uplinks))
    if stranded:
        return _check("topology_connected", False, f"Unreachable from the WAN: {', '.join(stranded)}.")
    return _check(
        "topology_connected", True,
        f"All {len(ids)} devices have a path to the WAN over {len(plan.links)} links.",
    )


def check_redundancy_present(plan: NetworkPlan) -> ValidationCheck:
    level = plan.spec.redundancy
    if level == RedundancyLevel.none:
        return _check("redundancy_present", True, "No redundancy requested; single WAN path is expected.")

    problems = []
    details = []

    # Dual WAN: two uplinks, each landing on a different router.
    router_ids = set(node_ids_of_type(plan, "router"))
    uplink_router = {}
    for l in plan.links:
        if l.link_type in ("wan", "redundant_wan"):
            ends = {l.source_id, l.target_id}
            router = ends & router_ids
            uplink = ends - router_ids
            if router and uplink:
                uplink_router[uplink.pop()] = router.pop()
    distinct_routers = set(uplink_router.values())
    if len(uplink_router) < 2 or len(distinct_routers) < 2:
        problems.append(
            f"dual WAN requires 2 uplinks on 2 separate routers, found {len(uplink_router)} "
            f"uplink(s) on {len(distinct_routers)} router(s)"
        )
    else:
        details.append("dual WAN (" + ", ".join(f"{u}/{r}" for u, r in sorted(uplink_router.items())) + ")")

    # Switch redundancy: two cores, every access switch dual-homed.
    if level == RedundancyLevel.dual_wan_plus_switch_redundancy:
        cores = set(node_ids_of_type(plan, "core_switch"))
        adj = _adjacency(plan)
        # 0 core uplinks is a disconnected switch, which topology_connected reports.
        single_homed = [a for a in node_ids_of_type(plan, "access_switch") if len(adj[a] & cores) == 1]
        if len(cores) < 2:
            problems.append(f"switch redundancy requires 2 core switches, found {len(cores)}")
        elif single_homed:
            problems.append(f"access switches with a single core uplink: {', '.join(single_homed)}")
        else:
            details.append(f"{len(cores)} core switches with every access switch dual-homed")

    if problems:
        return _check("redundancy_present", False, "; ".join(problems) + ".")
    return _check("redundancy_present", True, "Confirmed " + " and ".join(details) + ".")


def check_guest_isolation(plan: NetworkPlan) -> ValidationCheck:
    """
    The schema doesn't carry per-link VLAN membership, so isolation is
    checked structurally: guest has its own VLAN and disjoint subnet, and a
    firewall sits on every path between the LAN and the WAN to enforce the
    inter-VLAN policy.
    """
    if not plan.spec.guest_wifi_isolated:
        return _check("guest_isolation", True, "Guest isolation not requested.")

    guest = next((v for v in plan.vlans if v.name == "guest"), None)
    if guest is None:
        return _check("guest_isolation", False, "Guest isolation requested but there is no guest VLAN.")

    subnets = _parsed_subnets(plan)
    guest_net = next((net for v, net in subnets if v is guest), None)
    if guest_net is None:
        return _check("guest_isolation", False, f"Guest VLAN {guest.vlan_id} has an invalid subnet.")
    shared = [
        f"VLAN {v.vlan_id}" for v, net in subnets
        if v is not guest and net.overlaps(guest_net)
    ]
    if shared:
        return _check("guest_isolation", False, f"Guest subnet {guest_net} overlaps {', '.join(shared)}.")

    firewalls = set(node_ids_of_type(plan, "firewall"))
    if not firewalls:
        return _check("guest_isolation", False, "No firewall to enforce guest isolation policy.")
    cores = set(node_ids_of_type(plan, "core_switch"))
    bypass = cores & _reachable(_adjacency(plan), set(node_ids_of_type(plan, "wan_uplink")), blocked=firewalls)
    if bypass:
        return _check(
            "guest_isolation", False,
            f"{', '.join(sorted(bypass))} can reach the WAN without passing the firewall.",
        )
    return _check(
        "guest_isolation", True,
        f"Guest VLAN {guest.vlan_id} ({guest_net}) is its own subnet and all WAN-bound traffic "
        f"passes {', '.join(sorted(firewalls))} -- nothing bypasses it.",
    )

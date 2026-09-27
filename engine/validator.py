"""
The validator: NetworkPlan -> ValidationReport.

Owner: Nyles. This is the "proof it's real" demo moment -- it should run a
fixed set of concrete checks and return pass/fail with reasons, not another
LLM call.

Day 4 task.
"""

import ipaddress

from shared.schema import NetworkPlan, ValidationReport, ValidationCheck, RedundancyLevel

PRIVATE_NETWORKS = tuple(
  ipaddress.IPv4Network(cidr)
  for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def validate_plan(plan: NetworkPlan) -> ValidationReport:
  """Run deterministic consistency and addressing checks on a network plan."""
  parsed_networks = []
  invalid_subnets = []
  for vlan in plan.vlans:
    try:
      network = ipaddress.IPv4Network(vlan.subnet_cidr, strict=True)
    except ValueError:
      invalid_subnets.append(vlan.name)
    else:
      parsed_networks.append((vlan, network))

  overlaps = []
  for index, (left_vlan, left_network) in enumerate(parsed_networks):
    for right_vlan, right_network in parsed_networks[index + 1:]:
      if left_network.overlaps(right_network):
        overlaps.append(f"{left_vlan.name}/{right_vlan.name}")

  checks = [
    ValidationCheck(
      check_name="no_subnet_overlap",
      passed=not overlaps,
      detail=(
        "VLAN subnets do not overlap."
        if not overlaps
        else f"Overlapping VLAN subnet pairs: {', '.join(overlaps)}."
      ),
    ),
    ValidationCheck(
      check_name="valid_ranges",
      passed=(
        bool(plan.vlans)
        and not invalid_subnets
        and all(
          any(network.subnet_of(private) for private in PRIVATE_NETWORKS)
          for _, network in parsed_networks
        )
      ),
      detail=(
        "All VLAN subnets are valid IPv4 networks within RFC1918 private ranges."
        if plan.vlans
        and not invalid_subnets
        and all(
          any(network.subnet_of(private) for private in PRIVATE_NETWORKS)
          for _, network in parsed_networks
        )
        else "Every VLAN needs a valid RFC1918 IPv4 subnet."
        + (f" Invalid CIDRs: {', '.join(invalid_subnets)}." if invalid_subnets else "")
      ),
    ),
    ValidationCheck(
      check_name="subnet_capacity",
      passed=bool(plan.vlans) and all(
        network.num_addresses - 2 >= plan.spec.user_count + 1
        for _, network in parsed_networks
      ) and not invalid_subnets,
      detail=(
        f"Each VLAN has room for {plan.spec.user_count} users and a gateway."
        if plan.vlans
        and not invalid_subnets
        and all(
          network.num_addresses - 2 >= plan.spec.user_count + 1
          for _, network in parsed_networks
        )
        else f"Each VLAN must provide at least {plan.spec.user_count + 1} usable addresses."
      ),
    ),
  ]

  vlan_ids = [vlan.vlan_id for vlan in plan.vlans]
  valid_vlan_ids = bool(vlan_ids) and all(1 <= vlan_id <= 4094 for vlan_id in vlan_ids)
  checks.append(ValidationCheck(
    check_name="vlan_ids_unique",
    passed=valid_vlan_ids and len(vlan_ids) == len(set(vlan_ids)),
    detail=(
      "VLAN IDs are unique and in the usable range 1-4094."
      if valid_vlan_ids and len(vlan_ids) == len(set(vlan_ids))
      else "VLAN IDs must be unique and in the usable range 1-4094."
    ),
  ))

  node_ids = [node.node_id for node in plan.nodes]
  known_node_ids = set(node_ids)
  unresolved_links = [
    f"{link.source_id}->{link.target_id}"
    for link in plan.links
    if link.source_id not in known_node_ids or link.target_id not in known_node_ids
  ]
  valid_topology = (
    bool(node_ids)
    and bool(plan.links)
    and len(node_ids) == len(known_node_ids)
    and not unresolved_links
  )
  topology_detail = "Topology links reference existing, uniquely identified nodes."
  if not valid_topology:
    issues = []
    if not node_ids or not plan.links:
      issues.append("topology must contain nodes and links")
    if len(node_ids) != len(known_node_ids):
      issues.append("node IDs must be unique")
    if unresolved_links:
      issues.append(f"unresolved links: {', '.join(unresolved_links)}")
    topology_detail = "; ".join(issues) + "."
  checks.append(ValidationCheck(
    check_name="topology_references",
    passed=valid_topology,
    detail=topology_detail,
  ))

  if plan.spec.redundancy == RedundancyLevel.none:
    redundancy_present = True
    redundancy_detail = "No redundancy requirement was requested."
  else:
    nodes_by_id = {node.node_id: node for node in plan.nodes}

    def routers_on_wan_links(link_type):
      router_ids = set()
      for link in plan.links:
        if link.link_type != link_type:
          continue
        source = nodes_by_id.get(link.source_id)
        target = nodes_by_id.get(link.target_id)
        if source is None or target is None:
          continue
        if source.node_type == "router" and target.node_type in {"firewall", "wan_uplink"}:
          router_ids.add(source.node_id)
        elif target.node_type == "router" and source.node_type in {"firewall", "wan_uplink"}:
          router_ids.add(target.node_id)
      return router_ids

    wan_router_ids = routers_on_wan_links("wan")
    redundant_wan_router_ids = routers_on_wan_links("redundant_wan")
    dual_wan_present = bool(wan_router_ids and redundant_wan_router_ids) and (
      wan_router_ids.isdisjoint(redundant_wan_router_ids)
    )
    switch_redundancy_present = True
    if plan.spec.redundancy == RedundancyLevel.dual_wan_plus_switch_redundancy:
      core_ids = {
        node.node_id for node in plan.nodes if node.node_type == "core_switch"
      }
      access_ids = {
        node.node_id for node in plan.nodes if node.node_type == "access_switch"
      }
      firewall_ids = {
        node.node_id for node in plan.nodes if node.node_type == "firewall"
      }
      trunk_pairs = {
        frozenset((link.source_id, link.target_id))
        for link in plan.links
        if link.link_type == "trunk"
      }
      switch_redundancy_present = (
        len(core_ids) >= 2
        and bool(access_ids)
        and all(
          sum(frozenset((core_id, access_id)) in trunk_pairs for core_id in core_ids) >= 2
          for access_id in access_ids
        )
        and all(
          any(frozenset((firewall_id, core_id)) in trunk_pairs for firewall_id in firewall_ids)
          for core_id in core_ids
        )
      )
    redundancy_present = dual_wan_present and switch_redundancy_present
    redundancy_detail = (
      "Requested WAN and switch redundancy is present."
      if redundancy_present
      else "Topology is missing a distinct redundant WAN path or required redundant core links."
    )
  checks.append(ValidationCheck(
    check_name="redundancy_present",
    passed=redundancy_present,
    detail=redundancy_detail,
  ))

  if not plan.spec.guest_wifi_isolated:
    guest_isolation_passed = True
    guest_isolation_detail = "Guest Wi-Fi isolation was not required."
  else:
    guest_vlans = [vlan for vlan in plan.vlans if vlan.name.casefold() == "guest"]
    guest_isolation_passed = False
    if len(guest_vlans) != 1:
      guest_isolation_detail = "An isolated guest requirement needs exactly one guest VLAN."
    else:
      guest_isolation_detail = (
        "Guest has a dedicated VLAN, but the plan schema does not represent inter-VLAN "
        "access policy, so isolation cannot be verified."
      )
  checks.append(ValidationCheck(
    check_name="guest_isolation",
    passed=guest_isolation_passed,
    detail=guest_isolation_detail,
  ))

  return ValidationReport(
    overall_pass=all(check.passed for check in checks),
    checks=checks,
  )

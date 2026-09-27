"""
The generator: NetworkSpec -> NetworkPlan.

Owner: Nyles. This is pure Python -- no API, no LLM calls -- so it's fully
testable standalone (Day 2 task: hand Samir a working generate_plan()
function by end of Day 3).
"""

from shared.schema import NetworkSpec, NetworkPlan, VLANAllocation, TopologyNode, TopologyLink
from engine.taxonomy import suggest_prefix_length


def generate_plan(spec: NetworkSpec) -> NetworkPlan:
    """
    Turn a structured spec into a concrete IP/VLAN + topology plan.

    TODO (Nyles):
      - Allocate non-overlapping subnets per department_segments (+ guest
        if needs_guest_wifi) using suggest_prefix_length().
      - Build topology nodes or a router/firewall, core switch, access
        switches, APs -- scaled to user_count.
      - Add redundant WAN links / redundant core switch links if
        spec.redundancy calls for it.
    """
    raise NotImplementedError("generate_plan: TODO")

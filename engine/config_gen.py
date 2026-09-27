"""
Cisco-style config generator: NetworkPlan -> list[DeviceConfig].

Owner: Nyles. Day 5 task. Only run this on a plan that has already
passed validate_plan() -- don't generate configs for an invalid design.
"""

from shared.schema import NetworkPlan, DeviceConfig


def generate_configs(plan: NetworkPlan) -> list[DeviceConfig]:
    """
    TODO (Nyles): for each node in plan.nodes, emit Cisco IOS-style config
    text (VLAN definitions, interface assignments, trunk config, etc).

    Stretch goal: feed the output into containerlab to actually boot and
    verify the config instead of just printing text that looks plausible.
    """
    raise NotImplementedError("generate_configs: TODO")

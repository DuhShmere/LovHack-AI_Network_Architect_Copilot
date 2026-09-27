"""
The validator: NetworkPlan -> ValidationReport.

Owner: Nyles. This is the "proof it's real" demo moment -- it should run a
fixed set of concrete checks and return pass/fail with reasons, not another
LLM call.

Day 4 task.
"""

from shared.schema import NetworkPlan, ValidationReport, ValidationCheck


def validate_plan(plan: NetworkPlan) -> ValidationReport:
    """
    TODO (Nyles): implement real checks, e.g.:
      - no_subnet_overlap: pairwise check that no two VLAN subnets overlap
      - valid_ranges: every subnet_cidr parses and fits within a sane
        private range (RFC1918)
      - redundancy_present: if spec.redundancy != none, confirm the
        topology actually has redundant WAN/switch links
      - guest_isolation: if guest_wifi_isolated, confirm the guest VLAN
        has no direct link/trunk to staff/server VLANs
    """
    raise NotImplementedError("validate_plan: TODO")

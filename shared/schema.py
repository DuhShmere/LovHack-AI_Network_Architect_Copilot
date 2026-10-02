"""
THE CONTRACT.

This file is the single source of truth for the data shapes that pass
between Nyles's engine/ and Samir's pipeline/. Agree on this together
on Day 1 (Sept 26). After that, changes to this file should be rare and
should always be discussed first -- it's the one place both of you touch.

Everything downstream (LLM output, generator input/output, validator
input/output, config generator input) is typed against these models so
mismatches show up as import/type errors immediately, not as silent bugs
during integration on Day 8.
"""

from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. INPUT: the structured spec the LLM layer produces from plain English.
#    Samir's llm_layer.py produces this. Nyles's generator.py consumes it.
# ---------------------------------------------------------------------------

class RedundancyLevel(str, Enum):
    none = "none"
    dual_wan = "dual_wan"
    dual_wan_plus_switch_redundancy = "dual_wan_plus_switch_redundancy"


class NetworkSpec(BaseModel):
    """Structured requirements, extracted from a plain-English description."""

    org_name: str = Field(..., description="e.g. 'Acme Dental Office'")
    user_count: int = Field(..., description="Total people needing network access")

    # Segmentation requirements
    needs_guest_wifi: bool = False
    guest_wifi_isolated: bool = False
    department_segments: list[str] = Field(
        default_factory=list,
        description="e.g. ['staff', 'admin', 'iot', 'guest']",
    )

    # Redundancy / resilience
    redundancy: RedundancyLevel = RedundancyLevel.none

    # Addressing constraints
    preferred_base_cidr: Optional[str] = Field(
        None, description="e.g. '10.0.0.0/16' if the org has a preference, else None"
    )

    # Free-text notes the generator/validator can't structure but a human should see
    raw_notes: Optional[str] = None

    # What the LLM inferred or defaulted rather than read directly, so a human
    # can check it (e.g. "264 users = 214 staff + 38 contractors + 12 interns").
    # Informational only: the engine never reads it.
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. OUTPUT: what the generator produces from a NetworkSpec.
#    Nyles's generator.py produces this. Everything downstream consumes it.
# ---------------------------------------------------------------------------

class VLANAllocation(BaseModel):
    vlan_id: int
    name: str
    subnet_cidr: str
    purpose: str


class TopologyNode(BaseModel):
    node_id: str
    node_type: str  # "router" | "core_switch" | "access_switch" | "firewall" | "ap" | "wan_uplink"
    label: str


class TopologyLink(BaseModel):
    source_id: str
    target_id: str
    link_type: str  # "trunk" | "access" | "wan" | "redundant_wan" | "failover" (firewall pair state link)


class NetworkPlan(BaseModel):
    spec: NetworkSpec
    vlans: list[VLANAllocation]
    nodes: list[TopologyNode]
    links: list[TopologyLink]


# ---------------------------------------------------------------------------
# 3. OUTPUT: what the validator produces from a NetworkPlan.
#    Nyles's validator.py produces this. The API surfaces it as the
#    "proof it's real" demo moment.
# ---------------------------------------------------------------------------

class ValidationCheck(BaseModel):
    check_name: str  # e.g. "no_subnet_overlap", "redundant_wan_present"
    passed: bool
    detail: str


class ValidationReport(BaseModel):
    overall_pass: bool
    checks: list[ValidationCheck]


# ---------------------------------------------------------------------------
# 4. OUTPUT: Cisco-style config text, keyed by device node_id.
#    Nyles's config_gen.py produces this.
# ---------------------------------------------------------------------------

class DeviceConfig(BaseModel):
    node_id: str
    config_text: str


# ---------------------------------------------------------------------------
# 5. What the API ultimately returns to the dashboard.
#    Samir's api/routes.py assembles this from the pieces above.
# ---------------------------------------------------------------------------

class FullResult(BaseModel):
    plan: NetworkPlan
    validation: ValidationReport
    configs: list[DeviceConfig]

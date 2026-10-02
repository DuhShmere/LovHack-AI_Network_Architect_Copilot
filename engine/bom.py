"""
Bill of materials: NetworkPlan -> what to buy and roughly what it costs.

Owner: Nyles. Quantities come straight from the generated topology; prices
are rough budgetary figures for generic device classes (no vendor SKUs),
so the dashboard can show the scale of the spend, not a quote.
"""

from pydantic import BaseModel

from shared.schema import NetworkPlan

# node_type -> (item, description, unit cost USD, recurring monthly?)
CATALOG = {
    "router": ("Branch edge router", "1 GbE WAN, NAT, IP SLA tracking", 1_500, False),
    "firewall": ("Next-gen firewall appliance", "~1 Gbps inspected throughput, inter-VLAN policy", 3_000, False),
    "core_switch": ("Layer 3 core switch", "48x 1G + 4x 10G uplinks, static routing, HSRP, DHCP", 7_000, False),
    "access_switch": ("48-port PoE+ access switch", "802.1Q trunks to the core, PoE+ for APs and phones", 3_500, False),
    "ap": ("Wi-Fi 6 access point", "Ceiling mount, PoE powered, multi-SSID", 700, False),
    "wan_uplink": ("Business internet circuit", "Per month, per circuit", 400, True),
}
CABLE_DROP = ("Cat6 cable drop, installed", "One per user plus one per AP", 200)

NOTES = [
    "Budgetary estimates in USD for planning only -- get vendor quotes before buying.",
    "Excludes software licensing, support contracts, racks, UPS and installation labor beyond cable drops.",
]


class BomLine(BaseModel):
    item: str
    description: str
    quantity: int
    unit_cost: int
    subtotal: int
    recurring: bool = False  # True: monthly cost, not one-time


class BillOfMaterials(BaseModel):
    lines: list[BomLine]
    one_time_total: int
    monthly_total: int
    notes: list[str]


def bill_of_materials(plan: NetworkPlan) -> BillOfMaterials:
    counts: dict[str, int] = {}
    for node in plan.nodes:
        counts[node.node_type] = counts.get(node.node_type, 0) + 1

    lines = []
    for node_type, (item, description, unit, recurring) in CATALOG.items():
        qty = counts.get(node_type, 0)
        if qty:
            lines.append(BomLine(item=item, description=description, quantity=qty,
                                 unit_cost=unit, subtotal=qty * unit, recurring=recurring))

    drops = plan.spec.user_count + counts.get("ap", 0)
    item, description, unit = CABLE_DROP
    lines.append(BomLine(item=item, description=description, quantity=drops, unit_cost=unit, subtotal=drops * unit))

    unpriced = sorted(set(counts) - set(CATALOG))
    notes = NOTES + ([f"Not priced: {', '.join(unpriced)}."] if unpriced else [])
    return BillOfMaterials(
        lines=lines,
        one_time_total=sum(l.subtotal for l in lines if not l.recurring),
        monthly_total=sum(l.subtotal for l in lines if l.recurring),
        notes=notes,
    )

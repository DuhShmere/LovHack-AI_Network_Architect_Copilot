"""
Parts list: NetworkPlan -> exactly what to get to build it.

Owner: Nyles. Every quantity and every spec ("needs 46 ports", "PoE budget
of at least 180 W") is worked out from the generated topology, not guessed,
so a buyer can hand it to a vendor. Example models match the Cisco
IOS-style configs we generate.
"""

import math
from collections import Counter

from pydantic import BaseModel

from shared.schema import NetworkPlan, RedundancyLevel
from engine.taxonomy import ACCESS_SWITCH_PORTS

AP_POE_WATTS = 25  # a Wi-Fi 6 AP on PoE+ draws up to ~25 W
PHONE_POE_WATTS = 7
MBPS_PER_USER = 2  # busy-hour internet demand per user, for sizing circuits
CIRCUIT_TIERS_MBPS = [100, 250, 500, 1000, 2000, 5000, 10000]
PATCH_PANEL_PORTS = 48
SMALL_RACK_UNITS = 12  # fits on a wall; anything bigger gets a floor rack
FLOOR_RACK_USABLE_UNITS = 40  # of 42U, leaving room for cable management
UPS_RACK_UNITS = 2

NOTES = [
    "Example models fit the generated Cisco IOS-style configs; any device meeting the "
    "\"what to look for\" specs works, but its config syntax may differ.",
    "Not included: software licenses and support contracts.",
]


class Part(BaseModel):
    category: str  # "Network equipment", "Wireless", "Cabling", "Rack and power", "Internet service"
    name: str
    look_for: str  # the specs this design needs, so any vendor's equivalent can be checked
    example: str  # a model that meets them
    why: str
    quantity: int


class PartsList(BaseModel):
    parts: list[Part]
    notes: list[str]


def parts_list(plan: NetworkPlan) -> PartsList:
    types = {n.node_id: n.node_type for n in plan.nodes}
    counts = Counter(types.values())
    ports = Counter(end for l in plan.links for end in (l.source_id, l.target_id))
    full_redundancy = plan.spec.redundancy == RedundancyLevel.dual_wan_plus_switch_redundancy
    has_voip = any(v.name == "voip" for v in plan.vlans)
    users = plan.spec.user_count

    parts: list[Part] = []

    def add(category, name, look_for, example, why, quantity):
        if quantity:
            parts.append(Part(
                category=category, name=name, look_for=look_for, example=example, why=why, quantity=quantity,
            ))

    # --- Network equipment ---------------------------------------------------
    add(
        "Network equipment", "Edge router",
        "2+ Gigabit Ethernet ports, NAT, static routing, DHCP client on the WAN port",
        "Cisco Catalyst 8200 Edge (C8200-1N-4T)",
        "Connects an ISP circuit and translates inside addresses to the internet"
        + (", one per circuit so either can fail" if counts["router"] > 1 else ""),
        counts["router"],
    )
    add(
        "Network equipment", "Firewall",
        "~1 Gbps inspected throughput, stateful inspection, inside/outside zones"
        + (", IP SLA tracking, active/standby failover" if counts["firewall"] > 1 else ""),
        "Cisco Firepower 1120",
        "Sits between the internet and every internal network and enforces what may cross"
        + (" -- a pair, so it isn't a single point of failure" if counts["firewall"] > 1 else ""),
        counts["firewall"],
    )
    core_ports = max((ports[n] for n, t in types.items() if t == "core_switch"), default=0)
    stack = max(1, math.ceil(core_ports / ACCESS_SWITCH_PORTS))
    add(
        "Network equipment", "Layer 3 core switch",
        f"{core_ports} Gigabit ports in use"
        + (f" (a stack of {stack} switches per core)" if stack > 1 else "")
        + ", inter-VLAN routing, DHCP server" + (", HSRP" if counts["core_switch"] > 1 else ""),
        "Cisco Catalyst 9300-48T",
        "Routes between the VLANs and is every device's default gateway"
        + ("; two, so either can fail" if counts["core_switch"] > 1 else ""),
        counts["core_switch"] * stack,
    )
    access = [n for n, t in types.items() if t == "access_switch"]
    if access:
        aps_on = Counter(
            end for l in plan.links for end in (l.source_id, l.target_id)
            if end in access and {types.get(l.source_id), types.get(l.target_id)} == {"access_switch", "ap"}
        )
        users_per_switch = math.ceil(users / len(access))
        poe = max(aps_on[a] for a in access) * AP_POE_WATTS
        poe += users_per_switch * PHONE_POE_WATTS if has_voip else 0
        add(
            "Network equipment", "48-port PoE+ access switch",
            f"{ACCESS_SWITCH_PORTS} Gigabit PoE+ ports, 802.1Q trunks"
            + (", voice VLAN" if has_voip else "")
            + f", PoE budget of at least {poe} W",
            "Cisco Catalyst 9200L-48P-4G",
            f"Gives each desk a port and powers the access points"
            + (" and phones" if has_voip else "")
            + f" (up to {users_per_switch} users per switch)",
            len(access),
        )

    # --- Wireless --------------------------------------------------------------
    add(
        "Wireless", "Wi-Fi 6 access point",
        "Wi-Fi 6 (802.11ax), PoE+ powered, multiple SSIDs mapped to VLANs",
        "Cisco Catalyst 9120AXI",
        f"Wireless coverage for about 25 people each"
        + (", with a separate guest SSID" if any(v.name == "guest" for v in plan.vlans) else ""),
        counts["ap"],
    )

    # --- Cabling -------------------------------------------------------------
    drops = users + counts["ap"]
    add(
        "Cabling", "Cat6 cable drop, installed",
        "Cat6, under 90 m per run, terminated on a patch panel and a wall jack",
        "Contractor-installed Cat6 run",
        "One per user plus one per access point",
        drops,
    )
    panels = math.ceil(drops / PATCH_PANEL_PORTS)
    add(
        "Cabling", "48-port Cat6 patch panel",
        "1U, 48 ports, Cat6 rated",
        "Panduit or Leviton 48-port Cat6 panel",
        "Terminates the cable drops in the rack",
        panels,
    )
    add(
        "Cabling", "Cat6 patch cable",
        "Cat6, 1-2 m, assorted colors",
        "Any Cat6 patch cord",
        f"{drops} from the patch panels to switch ports, {len(plan.links)} between devices",
        drops + len(plan.links),
    )

    # --- Rack and power --------------------------------------------------------
    rack_devices = counts["router"] + counts["firewall"] + counts["core_switch"] * stack + len(access)
    upses_per_rack = 2 if full_redundancy else 1
    units = rack_devices + panels + upses_per_rack * UPS_RACK_UNITS
    if units <= SMALL_RACK_UNITS:
        racks = 1
        add(
            "Rack and power", "12U wall-mount rack",
            f"12U, 19-inch, lockable ({units}U needed)",
            "StarTech or Tripp Lite 12U wall-mount cabinet",
            "Holds the equipment, patch panels and UPS",
            racks,
        )
    else:
        racks = math.ceil(units / FLOOR_RACK_USABLE_UNITS)
        add(
            "Rack and power", "42U floor rack",
            f"42U, 19-inch, 1000 mm deep ({units}U needed in total)",
            "APC NetShelter SX 42U",
            "Holds the equipment, patch panels and UPS",
            racks,
        )
    add(
        "Rack and power", "Rack-mount UPS",
        "1500 VA, 2U rack-mount, network management card",
        "APC Smart-UPS SMT1500RM2U",
        "Keeps the network up through short power cuts"
        + ("; two per rack, one per power feed, so a UPS isn't a single point of failure"
           if full_redundancy else ""),
        racks * upses_per_rack,
    )

    # --- Internet service ------------------------------------------------------
    need = users * MBPS_PER_USER
    tier = next((t for t in CIRCUIT_TIERS_MBPS if t >= need), CIRCUIT_TIERS_MBPS[-1])
    speed = f"{tier // 1000} Gbps" if tier >= 1000 else f"{tier} Mbps"
    add(
        "Internet service", "Business internet circuit",
        f"{speed} or faster, static or DHCP addressing, business SLA"
        + ("; the second from a different provider over a different physical path"
           if counts["wan_uplink"] > 1 else ""),
        "Business fiber from a local ISP",
        f"About {MBPS_PER_USER} Mbps per user at peak"
        + ("; two circuits so one outage doesn't take the site offline" if counts["wan_uplink"] > 1 else ""),
        counts["wan_uplink"],
    )

    return PartsList(parts=parts, notes=NOTES)

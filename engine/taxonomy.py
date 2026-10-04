"""
Requirement taxonomy: the enums/constants that define what kinds of
requirements the LLM layer can extract and the generator can act on.

Owner: Nyles.

This is deliberately separate from shared/schema.py: schema.py is the
*data shapes* both sides agree on, this file is *domain knowledge*
(what counts as a valid VLAN purpose, what subnet sizes are sane for a
given user count, etc) that only the engine needs internally.
"""

import re

# Well-known segments -> (VLAN ID, purpose). Anything the LLM extracts that
# isn't in here (e.g. "admin", "finance") gets a VLAN from CUSTOM_VLAN_START.
SEGMENT_CATALOG = {
    "staff": (10, "Staff workstations"),
    "guest": (20, "Guest wifi"),
    "iot": (30, "IoT devices"),
    "voip": (40, "VoIP phones"),
    "servers": (50, "On-prem servers"),
    "management": (99, "Device management"),
}
CUSTOM_VLAN_START = 100
CUSTOM_VLAN_STEP = 10

# Other names the LLM uses for catalog segments. Without these, "guest wifi"
# as a department became a second, un-isolated guest network next to the
# real one.
SEGMENT_ALIASES = {
    **dict.fromkeys(
        ["guests", "guestwifi", "guest-wifi", "guest-wi-fi", "guest-wireless", "guest-network",
         "visitor", "visitors", "visitor-wifi", "visitor-wi-fi", "public-wifi", "public-wi-fi"],
        "guest",
    ),
    **dict.fromkeys(["employee", "employees", "corporate"], "staff"),
    **dict.fromkeys(["voice", "phones", "ip-phones", "voip-phones", "telephony"], "voip"),
    **dict.fromkeys(["server", "server-room", "datacenter", "data-center"], "servers"),
    **dict.fromkeys(["internet-of-things", "iot-devices", "smart-devices"], "iot"),
    **dict.fromkeys(["mgmt", "network-management", "device-management"], "management"),
}

# Rough subnet sizing guidance: user_count -> suggested host bits.
# e.g. up to 30 users fits a /27 (30 usable hosts).
SUBNET_SIZE_GUIDANCE = [
    (30, 27),
    (62, 26),
    (126, 25),
    (254, 24),
    (510, 23),
]

# Topology scaling: 48-port access switches, enough of them that every user
# gets a port after the core uplinks and APs take theirs; one AP per this
# many wireless clients.
ACCESS_SWITCH_PORTS = 48
CLIENTS_PER_AP = 25


def suggest_prefix_length(host_count: int) -> int:
    """Return a CIDR prefix length that comfortably fits host_count hosts."""
    for max_hosts, prefix in SUBNET_SIZE_GUIDANCE:
        if host_count <= max_hosts:
            return prefix
    # Beyond the table: smallest prefix whose usable hosts (2^bits - 2) fit.
    host_bits = (host_count + 1).bit_length()
    return 32 - host_bits


def normalize_segment(name: str) -> str:
    """'Front Desk ' -> 'front-desk', 'Guest WiFi' -> 'guest', so LLM output
    maps to stable VLAN names."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return SEGMENT_ALIASES.get(slug, slug)

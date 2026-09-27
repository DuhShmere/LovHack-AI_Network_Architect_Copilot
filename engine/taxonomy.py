"""
Requirement taxonomy: the enums/constants that define what kinds of
requirements the LLM layer can extract and the generator can act on.

Owner: Nyles. Day 1 task.

This is deliberately separate from shared/schema.py: schema.py is the
*data shapes* both sides agree on, this file is *domain knowledge*
(what counts as a valid VLAN purpose, what subnet sizes are sane for a
given user count, etc) that only the engine needs internally.
"""

# Example starting point -- replace/expand with real taxonomy.

STANDARD_SEGMENTS = [
    "staff",
    "guest",
    "iot",
    "voip",
    "servers",
    "management",
]

# Rough subnet sizing guidance: user_count -> suggested host bits.
# e.g. up to 30 users fits a /27 (30 usable hosts).
SUBNET_SIZE_GUIDANCE = [
    (30, 27),
    (62, 26),
    (126, 25),
    (254, 24),
    (510, 23),
]

MAX_FALLBACK_SUBNET_HOSTS = (1 << 16) - 2


def suggest_prefix_length(host_count: int) -> int:
    """Return a CIDR prefix length that comfortably fits host_count hosts."""
    if not 1 <= host_count <= MAX_FALLBACK_SUBNET_HOSTS:
        raise ValueError(
            f"host_count must be between 1 and {MAX_FALLBACK_SUBNET_HOSTS} for IPv4 allocation"
        )
    for max_hosts, prefix in SUBNET_SIZE_GUIDANCE:
        if host_count <= max_hosts:
            return prefix
    return 16

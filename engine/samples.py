"""
Built-in sample designs: ready-made specs that run through the real
engine without an LLM call.

Owner: Nyles. They let the dashboard demo everything downstream of parsing
(validation, simulator, cost, configs, break-it) even with no API key or
no network -- and show the contrast between a small single-path office and
a fully redundant one.
"""

from pydantic import BaseModel

from shared.schema import NetworkSpec, RedundancyLevel


class SampleInfo(BaseModel):
    key: str
    label: str
    description: str  # the plain-English request this spec stands for


_SAMPLES: dict[str, tuple[SampleInfo, NetworkSpec]] = {}


def _sample(key: str, label: str, description: str, spec: NetworkSpec) -> None:
    _SAMPLES[key] = (SampleInfo(key=key, label=label, description=description), spec)


_sample(
    "vet_hospital",
    "24/7 vet hospital, fully redundant",
    "Northwind Veterinary Hospital: 58 full-time staff, 22 part-time techs and 10 visiting "
    "specialists; 6 billing staff are fully remote. Separate networks for clinical, front desk "
    "and lab/IoT devices (kennel cameras, smart locks, X-ray). Isolated guest Wi-Fi for the "
    "waiting room. We're a 24/7 emergency hospital: two internet providers and no single switch "
    "failure taking us down. Use 10.50.0.0/16. Voice QoS for the front desk, 802.1X in exam rooms.",
    NetworkSpec(
        org_name="Northwind Veterinary Hospital",
        user_count=90,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["clinical", "front-desk", "iot"],
        redundancy=RedundancyLevel.dual_wan_plus_switch_redundancy,
        preferred_base_cidr="10.50.0.0/16",
        raw_notes="Voice QoS for the front desk phones; 802.1X on exam room ports.",
        assumptions=[
            "user_count 90 = 58 full-time + 22 part-time techs + 10 visiting specialists; "
            "6 remote billing staff excluded",
            "Full redundancy (dual WAN + redundant core switches) from '24/7' and "
            "'no single switch failure'",
            "Kennel cameras, smart locks and the X-ray machine grouped into one IoT segment",
        ],
    ),
)

_sample(
    "dental_office",
    "Small dental office, no redundancy",
    "Bright Smile Dental: 18 staff, guest Wi-Fi for patients kept off our systems, and a "
    "separate network for the X-ray sensors. One internet connection is fine.",
    NetworkSpec(
        org_name="Bright Smile Dental",
        user_count=18,
        needs_guest_wifi=True,
        guest_wifi_isolated=True,
        department_segments=["iot"],
        redundancy=RedundancyLevel.none,
        assumptions=["X-ray sensors placed on an IoT segment"],
    ),
)


def list_samples() -> list[SampleInfo]:
    return [info for info, _ in _SAMPLES.values()]


def sample_spec(key: str) -> NetworkSpec | None:
    entry = _SAMPLES.get(key)
    return entry[1].model_copy(deep=True) if entry else None

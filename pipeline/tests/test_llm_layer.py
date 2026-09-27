"""Samir: tests for the LLM extraction layer against varied plain-English inputs."""

from pipeline.llm_layer import parse_requirements


def test_parses_basic_office_description():
    result = parse_requirements(
        "50-person office, guest wifi isolated, redundant WAN"
    )
    assert result.user_count == 50
    assert result.needs_guest_wifi is True
    assert result.guest_wifi_isolated is True

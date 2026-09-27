"""Tests for engine requirement taxonomy and subnet sizing."""

import pytest

from engine.taxonomy import suggest_prefix_length


@pytest.mark.parametrize(("host_count", "expected_prefix"), [
    (1, 27),
    (30, 27),
    (31, 26),
    (62, 26),
    (63, 25),
    (510, 23),
    (511, 16),
    (65534, 16),
])
def test_suggest_prefix_length_boundaries(host_count, expected_prefix):
    assert suggest_prefix_length(host_count) == expected_prefix


@pytest.mark.parametrize("host_count", [0, -1, 65535])
def test_suggest_prefix_length_rejects_unrepresentable_host_counts(host_count):
    with pytest.raises(ValueError):
        suggest_prefix_length(host_count)
"""Tests for network telemetry and listener tracking."""
from app.broadcast import CaptionBroadcaster


def test_telemetry_recording_and_aggregation():
    b = CaptionBroadcaster()

    # Record local client RTTs
    b.record_rtt("localhost", 12.5, client_id="c_local1")
    b.record_rtt("skc.local", 15.0, client_id="c_local2")

    # Record public client RTTs
    b.record_rtt("live.starkvillekoreanchurch.org", 85.0, client_id="c_pub1")
    b.record_rtt("live.starkvillekoreanchurch.org", 95.0, client_id="c_pub2")

    stats = b.get_telemetry_stats()
    assert stats["local_listeners"] == 2
    assert stats["public_listeners"] == 2
    assert stats["total_listeners"] == 4
    assert stats["local_rtt_ms"] == 14  # median of 12.5 and 15.0 rounded
    assert stats["public_rtt_ms"] == 90  # median of 85.0 and 95.0 rounded


def test_telemetry_input_validation():
    b = CaptionBroadcaster()

    # Invalid RTT values should be ignored
    b.record_rtt("localhost", -10, client_id="c1")
    b.record_rtt("localhost", 999999, client_id="c2")

    stats = b.get_telemetry_stats()
    assert stats["local_samples"] == 0
    assert stats["local_rtt_ms"] is None

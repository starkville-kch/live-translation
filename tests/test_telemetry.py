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


def test_language_switch_updates_listeners_by_target():
    """
    Attendee switches ZH -> EN -> ZH using same client_id.
    listeners_by_target must move atomically; total stays at 1.
    """
    b = CaptionBroadcaster()

    # Initial state: attendee on Chinese
    b.record_rtt("localhost", 20.0, client_id="abc123", target_lang="zh")
    stats = b.get_telemetry_stats()
    assert stats["listeners_by_target"].get("zh", 0) == 1
    assert stats["listeners_by_target"].get("en", 0) == 0
    assert stats["total_listeners"] == 1

    # Attendee switches ZH -> EN via target_changed
    b.update_target("abc123", "en")
    stats = b.get_telemetry_stats()
    assert stats["listeners_by_target"].get("en", 0) == 1
    assert stats["listeners_by_target"].get("zh", 0) == 0
    assert stats["total_listeners"] == 1

    # Attendee switches back EN -> ZH
    b.update_target("abc123", "zh")
    stats = b.get_telemetry_stats()
    assert stats["listeners_by_target"].get("zh", 0) == 1
    assert stats["listeners_by_target"].get("en", 0) == 0
    assert stats["total_listeners"] == 1


def test_update_target_unknown_client_is_noop():
    """update_target for an unseen client_id must not create a phantom listener."""
    b = CaptionBroadcaster()
    b.update_target("ghost_client", "en")
    stats = b.get_telemetry_stats()
    assert stats["total_listeners"] == 0
    assert stats["listeners_by_target"] == {}

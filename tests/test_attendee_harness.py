import time
import pytest
from app.broadcast import (
    CaptionBroadcaster,
    CLIENT_INACTIVE_TTL_S,
    MOBILE_BACKGROUND_THROTTLE_INTERVAL_S,
)


def test_attendee_invariant_with_bounded_settle():
    """Verify eventual consistency invariant: local + public + unknown == total

    Even when clients connect via SSE before telemetry is reported, the unknown
    bucket accounts for the discrepancy, and settling yields exact counts.
    """
    b = CaptionBroadcaster()

    # 1. Simulate 4 attendee connections over SSE
    queues = [b.add_client() for _ in range(4)]
    stats = b.get_telemetry_stats()

    # Immediately on SSE connect with 0 telemetry reports:
    assert stats["total_listeners"] == 4
    assert stats["local_listeners"] == 0
    assert stats["public_listeners"] == 0
    assert stats["unknown_listeners"] == 4
    assert stats["local_listeners"] + stats["public_listeners"] + stats["unknown_listeners"] == stats["total_listeners"]

    # 2. First 2 clients report telemetry (1 local, 1 public)
    b.record_rtt("skc.local", 15.0, client_id="c1")
    b.record_rtt("live.starkvillekoreanchurch.org", 90.0, client_id="c2")

    stats = b.get_telemetry_stats()
    assert stats["local_listeners"] == 1
    assert stats["public_listeners"] == 1
    assert stats["unknown_listeners"] == 2
    assert stats["total_listeners"] == 4
    assert stats["local_listeners"] + stats["public_listeners"] + stats["unknown_listeners"] == stats["total_listeners"]

    # 3. Remaining 2 clients report telemetry
    b.record_rtt("skc.lan", 18.0, client_id="c3")
    b.record_rtt("live.starkvillekoreanchurch.org", 110.0, client_id="c4")

    # Bounded settle verification loop (poll until settled, timeout 2s)
    # Note: In-process record_rtt updates _active_clients synchronously, so this
    # loop breaks on iteration 1. It is retained intentionally as a guard to
    # prevent test flakiness if telemetry ingestion is ever dispatched asynchronously.
    deadline = time.monotonic() + 2.0
    settled = False
    while time.monotonic() < deadline:
        stats = b.get_telemetry_stats()
        if (
            stats["local_listeners"] == 2
            and stats["public_listeners"] == 2
            and stats["unknown_listeners"] == 0
            and stats["total_listeners"] == 4
        ):
            settled = True
            break
        time.sleep(0.05)

    assert settled, f"Telemetry did not settle in time: {stats}"
    assert stats["local_listeners"] + stats["public_listeners"] + stats["unknown_listeners"] == stats["total_listeners"]

    # Clean up
    for q in queues:
        b.remove_client(q)


def test_mobile_background_throttle_no_flap(monkeypatch):
    """Verify that mobile background throttle interval (~60s) does not flap

    when tested against the 120s CLIENT_INACTIVE_TTL_S (2.0:1 ratio, +100% margin).
    """
    b = CaptionBroadcaster()
    current_time = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: current_time)

    # Initial ping from a mobile browser
    b.record_rtt("skc.local", 20.0, client_id="mobile_user_1")
    stats = b.get_telemetry_stats()
    assert stats["local_listeners"] == 1

    # Mobile background throttle occurs: 65 seconds elapse without ping
    # (exceeding MOBILE_BACKGROUND_THROTTLE_INTERVAL_S of 60s)
    current_time += 65.0

    # Inactivity TTL is 120s, so the mobile user MUST NOT be pruned (no flapping)
    stats = b.get_telemetry_stats()
    assert stats["local_listeners"] == 1, "Listener was prematurely pruned during 65s mobile throttle"

    # Mobile browser fires its throttled ping at t=65s
    b.record_rtt("skc.local", 22.0, client_id="mobile_user_1")
    stats = b.get_telemetry_stats()
    assert stats["local_listeners"] == 1

    # Now simulate true abandonment: advance beyond CLIENT_INACTIVE_TTL_S past the last ping
    current_time += (CLIENT_INACTIVE_TTL_S + 5.0)
    stats = b.get_telemetry_stats()
    assert stats["local_listeners"] == 0, f"Inactive client was not pruned after exceeding {CLIENT_INACTIVE_TTL_S}s TTL"


def test_telemetry_surplus_warning(caplog):
    """Verify that when classified telemetry clients exceed SSE clients,

    a diagnostic warning is emitted rather than silently swallowed.
    """
    import logging
    b = CaptionBroadcaster()

    # Client reports telemetry with no active SSE connection (classified=1, sse=0)
    with caplog.at_level(logging.WARNING):
        b.record_rtt("skc.local", 25.0, client_id="ghost_client")
        stats = b.get_telemetry_stats()

    # The surplus is reflected in total and warning is logged
    assert stats["local_listeners"] == 1
    assert stats["total_listeners"] == 1
    assert stats["unknown_listeners"] == 0
    assert any("Telemetry surplus detected" in r.message for r in caplog.records)

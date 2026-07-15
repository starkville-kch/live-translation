"""
app/events.py — Operator Event Log
====================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
A lightweight, thread-safe in-memory ring buffer of operator-relevant
events (not developer logs).  The frontend polls /api/events to display
these in the Event Log card.

Event categories and icons
--------------------------
  success  🟢   Translation lifecycle milestones
  audio    🔵   Audio device state changes
  gemini   🟣   Gemini session lifecycle
  network  🟡   Reconnect / GoAway / internet issues
  user     👤   Attendee joins / leaves, operator actions
  warning  ⚠️   Non-fatal conditions (no signal, etc.)
  error    🔴   Failures requiring attention
"""
from collections import deque
import threading
import time

CATEGORY_ICONS = {
    "success": "🟢",
    "audio":   "🔵",
    "gemini":  "🟣",
    "network": "🟡",
    "user":    "👤",
    "warning": "⚠️",
    "error":   "🔴",
}


class OperatorEventLog:
    def __init__(self, maxlen: int = 50):
        self._deque: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._next_id: int = 0

    def add(self, category: str, message: str, details: dict | None = None) -> None:
        with self._lock:
            event = {
                "id":       self._next_id,
                "ts":       time.time(),
                "category": category,
                "icon":     CATEGORY_ICONS.get(category, "•"),
                "message":  message,
                "details":  details or {},
            }
            self._deque.append(event)
            self._next_id += 1

    def since(self, last_id: int) -> list:
        with self._lock:
            return [e for e in self._deque if e["id"] > last_id]

    @property
    def latest_id(self) -> int:
        with self._lock:
            return self._deque[-1]["id"] if self._deque else -1


operator_events = OperatorEventLog()

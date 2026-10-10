"""Small in-memory sliding-window rate limiter for expensive analysis work."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float, now: Optional[float] = None) -> float:
        """Return 0 if allowed, otherwise seconds until the oldest event expires."""
        now = time.monotonic() if now is None else now
        if limit <= 0:
            return max(window, 1.0)
        with self._lock:
            events = self._events[key]
            cutoff = now - window
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return max(0.0, events[0] + window - now)
            events.append(now)
            return 0.0

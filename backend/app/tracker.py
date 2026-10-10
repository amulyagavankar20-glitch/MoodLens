"""Keeps face identities stable between frames and smooths the mood scores,
so the UI doesn't flicker between happy/neutral every other frame."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import SMOOTHING_ALPHA, TRACK_TTL
from .engines import RawFace


@dataclass
class Track:
    id: int
    box: tuple
    scores: Dict[str, float]
    last_seen: float
    hits: int = 1
    mood: str = "neutral"


def _center(b):
    return (b[0] + b[2] / 2, b[1] + b[3] / 2)


def _iou(a, b) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


class FaceTracker:
    def __init__(self, alpha: float = SMOOTHING_ALPHA, ttl: float = TRACK_TTL):
        self.alpha = alpha
        self.ttl = ttl
        self.tracks: List[Track] = []
        self._next_id = 1

    def reset(self) -> None:
        self.tracks.clear()
        self._next_id = 1

    def update(self, faces: List[RawFace], now: Optional[float] = None) -> List[Track]:
        now = time.monotonic() if now is None else now
        self.tracks = [t for t in self.tracks if now - t.last_seen <= self.ttl]

        used = set()
        matched: List[Track] = []
        for f in faces:
            best, best_iou = None, 0.2  # min overlap to count as same face
            for t in self.tracks:
                if t.id in used:
                    continue
                v = _iou(f.box, t.box)
                if v > best_iou:
                    best, best_iou = t, v
            if best is None:
                t = Track(
                    id=self._next_id,
                    box=f.box,
                    scores=dict(f.scores),
                    last_seen=now,
                )
                self._next_id += 1
                self.tracks.append(t)
            else:
                t = best
                a = self.alpha
                keys = set(t.scores) | set(f.scores)
                t.scores = {
                    k: (1 - a) * t.scores.get(k, 0.0) + a * f.scores.get(k, 0.0)
                    for k in keys
                }
                # box smoothing too, otherwise the overlay jitters
                t.box = tuple(int((1 - a) * o + a * n) for o, n in zip(t.box, f.box))
                t.last_seen = now
                t.hits += 1
            used.add(t.id)
            t.mood = max(t.scores, key=t.scores.get)
            matched.append(t)
        return matched

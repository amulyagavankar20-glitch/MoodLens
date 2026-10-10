"""Mood engines. Each takes a BGR frame and returns a list of RawFace.

haar     -> the original OpenCV cascade approach. Fast, no extra deps,
            but it can only tell smiling from not smiling.
deepface -> optional CNN emotion model, gives all 7 moods. Needs
            `pip install deepface tf-keras` (big download).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Protocol

import cv2
import numpy as np

from .config import MOODS


@dataclass
class RawFace:
    box: tuple  # x, y, w, h in the frame we were given
    scores: Dict[str, float] = field(default_factory=dict)  # mood -> 0..1


class Engine(Protocol):
    name: str
    moods: List[str]

    def analyze(self, frame: np.ndarray) -> List[RawFace]: ...


class HaarEngine:
    name = "haar"
    moods = ["happy", "neutral"]

    def __init__(self) -> None:
        base = cv2.data.haarcascades
        self.face = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
        self.smile = cv2.CascadeClassifier(base + "haarcascade_smile.xml")
        if self.face.empty() or self.smile.empty():
            raise RuntimeError("could not load haar cascades from opencv")

    def analyze(self, frame: np.ndarray) -> List[RawFace]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self.face.detectMultiScale(
            gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60)
        )
        out: List[RawFace] = []
        for (x, y, w, h) in faces:
            # smiles live in the lower half of the face, looking there cuts false hits
            lower = gray[y + h // 2 : y + h, x : x + w]
            hits = self.smile.detectMultiScale(
                lower, scaleFactor=1.7, minNeighbors=18, minSize=(w // 5, h // 10)
            )
            # haar gives a yes/no, so fake a confidence from how many boxes agreed
            happy = 0.0 if len(hits) == 0 else min(0.95, 0.7 + 0.1 * len(hits))
            out.append(
                RawFace(
                    box=(int(x), int(y), int(w), int(h)),
                    scores={"happy": happy, "neutral": 1.0 - happy},
                )
            )
        return out


class DeepFaceEngine:
    name = "deepface"
    moods = list(MOODS)

    def __init__(self) -> None:
        try:
            from deepface import DeepFace  # noqa: WPS433
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "deepface not installed. run: pip install deepface tf-keras"
            ) from exc
        self._df = DeepFace

    def analyze(self, frame: np.ndarray) -> List[RawFace]:
        try:
            results = self._df.analyze(
                frame,
                actions=["emotion"],
                detector_backend="opencv",
                enforce_detection=False,
                silent=True,
            )
        except Exception:
            return []
        if isinstance(results, dict):
            results = [results]
        out: List[RawFace] = []
        for r in results:
            reg = r.get("region") or {}
            w, h = reg.get("w", 0), reg.get("h", 0)
            # enforce_detection=False returns the whole frame when nothing is found
            if w == 0 or h == 0 or (w >= frame.shape[1] - 2 and h >= frame.shape[0] - 2):
                continue
            emo = r.get("emotion", {})
            scores = {m: float(emo.get(m, 0.0)) / 100.0 for m in MOODS}
            out.append(RawFace(box=(reg["x"], reg["y"], w, h), scores=scores))
        return out


_REGISTRY = {"haar": HaarEngine, "deepface": DeepFaceEngine}
_CACHE: Dict[str, Engine] = {}


def available_engines() -> Dict[str, dict]:
    info = {"haar": {"available": True, "moods": HaarEngine.moods}}
    try:
        import deepface  # noqa: F401

        info["deepface"] = {"available": True, "moods": DeepFaceEngine.moods}
    except ImportError:
        info["deepface"] = {"available": False, "moods": DeepFaceEngine.moods}
    return info


def get_engine(name: str) -> Engine:
    if name not in _REGISTRY:
        raise KeyError(name)
    if name not in _CACHE:
        _CACHE[name] = _REGISTRY[name]()
    return _CACHE[name]

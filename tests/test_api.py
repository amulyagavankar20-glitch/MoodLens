import io
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.engines import RawFace  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.tracker import FaceTracker  # noqa: E402

client = TestClient(app)


def jpeg(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def blank() -> bytes:
    return jpeg(np.zeros((240, 320, 3), dtype=np.uint8))


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_engines_lists_haar():
    r = client.get("/api/engines").json()
    assert r["engines"]["haar"]["available"] is True


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "moodlens" in r.text.lower()
    assert client.get("/static/app.js").status_code == 200


def test_analyze_blank_image_has_no_faces():
    r = client.post("/api/analyze", files={"file": ("a.jpg", blank(), "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["faces"] == [] and body["width"] == 320


def test_analyze_rejects_garbage():
    r = client.post("/api/analyze", files={"file": ("a.jpg", b"nope", "image/jpeg")})
    assert r.status_code == 400


def test_analyze_unknown_engine():
    r = client.post(
        "/api/analyze?engine=nope", files={"file": ("a.jpg", blank(), "image/jpeg")}
    )
    assert r.status_code == 404


def test_websocket_roundtrip():
    with client.websocket_connect("/ws") as ws:
        ws.send_bytes(blank())
        msg = ws.receive_json()
        assert msg["type"] == "result" and msg["faces"] == []
        ws.send_text('{"engine": "bogus"}')
        assert ws.receive_json()["type"] == "error"


def test_tracker_keeps_id_and_smooths():
    t = FaceTracker(alpha=0.5)
    box = (10, 10, 100, 100)
    a = t.update([RawFace(box, {"happy": 0.0, "neutral": 1.0})], now=0.0)[0]
    assert a.mood == "neutral"
    b = t.update([RawFace(box, {"happy": 1.0, "neutral": 0.0})], now=0.1)[0]
    assert b.id == a.id
    assert b.scores["happy"] == pytest.approx(0.5)  # smoothed, not a hard flip


def test_tracker_expires_old_faces():
    t = FaceTracker(ttl=1.0)
    t.update([RawFace((0, 0, 50, 50), {"neutral": 1.0})], now=0.0)
    t.update([], now=5.0)
    assert t.tracks == []

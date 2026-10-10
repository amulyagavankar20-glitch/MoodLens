from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    ANALYZE_RATE_LIMIT,
    ANALYZE_RATE_WINDOW,
    DEFAULT_ENGINE,
    MAX_FRAME_WIDTH,
    MOOD_EMOJI,
    WS_FRAME_RATE_LIMIT,
    WS_FRAME_RATE_WINDOW,
)
from .engines import available_engines, get_engine
from .rate_limit import SlidingWindowRateLimiter
from .tracker import FaceTracker

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="MoodLens", version="1.0.0")
rate_limiter = SlidingWindowRateLimiter()


def client_key(client) -> str:
    # Do not trust X-Forwarded-For here: clients can spoof it unless a trusted
    # proxy middleware is configured in front of the app.
    return client.host if client else "unknown"


@app.middleware("http")
async def limit_analyze_requests(request, call_next):
    # Middleware runs before FastAPI parses the multipart upload body.
    if request.method == "POST" and request.url.path == "/api/analyze":
        retry_after = rate_limiter.check(
            client_key(request.client), ANALYZE_RATE_LIMIT, ANALYZE_RATE_WINDOW
        )
        if retry_after:
            return JSONResponse(
                status_code=429,
                content={"detail": "analysis rate limit exceeded"},
                headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
            )
    return await call_next(request)


def decode_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not a valid image")
    return img


def run_engine(engine_name: str, frame: np.ndarray, tracker: Optional[FaceTracker]):
    """Detect on a (possibly shrunk) frame, return payload in original coords."""
    h, w = frame.shape[:2]
    scale = 1.0
    if w > MAX_FRAME_WIDTH:
        scale = MAX_FRAME_WIDTH / w
        frame = cv2.resize(frame, (MAX_FRAME_WIDTH, int(h * scale)))

    engine = get_engine(engine_name)
    t0 = time.perf_counter()
    raw = engine.analyze(frame)
    ms = (time.perf_counter() - t0) * 1000

    if tracker is not None:
        items = [(t.id, t.box, t.scores, t.mood) for t in tracker.update(raw)]
    else:
        items = [
            (i + 1, f.box, f.scores, max(f.scores, key=f.scores.get))
            for i, f in enumerate(raw)
        ]

    faces = []
    for fid, box, scores, mood in items:
        faces.append(
            {
                "id": fid,
                "box": [int(v / scale) for v in box],
                "mood": mood,
                "emoji": MOOD_EMOJI.get(mood, "🙂"),
                "confidence": round(float(scores.get(mood, 0.0)), 3),
                "scores": {k: round(float(v), 3) for k, v in scores.items()},
            }
        )
    return {
        "engine": engine.name,
        "width": w,
        "height": h,
        "latency_ms": round(ms, 1),
        "faces": faces,
    }


# ---------- REST ----------

@app.get("/api/health")
def health():
    return {"status": "ok", "default_engine": DEFAULT_ENGINE}


@app.get("/api/engines")
def engines():
    return {"default": DEFAULT_ENGINE, "engines": available_engines()}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), engine: str = DEFAULT_ENGINE):
    """One-shot analysis of an uploaded image (no smoothing)."""
    try:
        frame = decode_image(await file.read())
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        return await asyncio.to_thread(run_engine, engine, frame, None)
    except KeyError:
        raise HTTPException(404, f"unknown engine '{engine}'")
    except RuntimeError as e:
        raise HTTPException(503, str(e))


# ---------- WebSocket ----------

@app.websocket("/ws")
async def ws_stream(ws: WebSocket):
    """Client sends binary JPEG frames, server answers with a JSON result per frame.
    Text messages are control: {"engine": "haar"|"deepface"} or {"reset": true}."""
    await ws.accept()
    engine_name = DEFAULT_ENGINE
    tracker = FaceTracker()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            if msg.get("text") is not None:
                try:
                    ctl = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if "engine" in ctl:
                    try:
                        await asyncio.to_thread(get_engine, ctl["engine"])
                        engine_name = ctl["engine"]
                        tracker.reset()
                        await ws.send_json({"type": "engine", "engine": engine_name})
                    except (KeyError, RuntimeError) as e:
                        await ws.send_json({"type": "error", "message": str(e)})
                if ctl.get("reset"):
                    tracker.reset()
                continue

            data = msg.get("bytes")
            if not data:
                continue
            retry_after = rate_limiter.check(
                client_key(ws.client), WS_FRAME_RATE_LIMIT, WS_FRAME_RATE_WINDOW
            )
            if retry_after:
                await ws.send_json({"type": "error", "message": "frame rate limit exceeded"})
                await ws.close(code=1013, reason="frame rate limit exceeded")
                break
            try:
                frame = decode_image(data)
            except ValueError:
                continue
            result = await asyncio.to_thread(run_engine, engine_name, frame, tracker)
            result["type"] = "result"
            await ws.send_json(result)
    except WebSocketDisconnect:
        pass


# ---------- frontend ----------

@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

import os

# which engine boots by default: "haar" (light, happy/neutral) or "deepface" (7 moods)
DEFAULT_ENGINE = os.getenv("MOODLENS_ENGINE", "haar")

# frames bigger than this get shrunk before detection (speed)
MAX_FRAME_WIDTH = int(os.getenv("MOODLENS_MAX_WIDTH", "640"))

# EMA weight for new readings. lower = smoother but laggier
SMOOTHING_ALPHA = float(os.getenv("MOODLENS_ALPHA", "0.45"))

# drop a tracked face after this many seconds without a match
TRACK_TTL = float(os.getenv("MOODLENS_TRACK_TTL", "1.5"))

# Per-client limits for expensive image analysis. These are in-memory and
# apply independently in each worker process.
ANALYZE_RATE_LIMIT = int(os.getenv("MOODLENS_ANALYZE_RATE_LIMIT", "30"))
ANALYZE_RATE_WINDOW = float(os.getenv("MOODLENS_ANALYZE_RATE_WINDOW", "60"))
WS_FRAME_RATE_LIMIT = int(os.getenv("MOODLENS_WS_FRAME_RATE_LIMIT", "10"))
WS_FRAME_RATE_WINDOW = float(os.getenv("MOODLENS_WS_FRAME_RATE_WINDOW", "1"))

MOODS = ["happy", "sad", "angry", "surprise", "fear", "disgust", "neutral"]

MOOD_EMOJI = {
    "happy": "😄",
    "sad": "😢",
    "angry": "😠",
    "surprise": "😲",
    "fear": "😨",
    "disgust": "🤢",
    "neutral": "😐",
}

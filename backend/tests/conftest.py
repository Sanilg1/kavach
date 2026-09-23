"""Tests run fully offline: local storage, JSON DB, mock brain, silent narration.
Settings are read at import time, so the environment is fixed before `app` is imported."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="kavach-tests-")
os.environ.update(
    KAVACH_ENV_FILE=os.path.join(_TMP, "no.env"),   # never pick up the developer's backend/.env
    KAVACH_MODE="local",
    KAVACH_STORAGE="local",
    KAVACH_DB="local",
    KAVACH_BRAIN="mock",
    KAVACH_TTS="mock",
    KAVACH_DATA_DIR=_TMP,
    KAVACH_WORK_DIR=os.path.join(_TMP, "work"),
    KAVACH_WORKERS="1",
    KAVACH_RATE_LIMIT_PER_HOUR="1000",
    KAVACH_VIDEO_FPS="8",
)
for k in ("GROQ_API_KEY", "GROQ_API_KEY_2", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEMO_PDF = BACKEND / "assets" / "demo_computer_networks.pdf"

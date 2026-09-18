"""Environment-driven settings. Every AWS dependency has a local fallback so the
whole pipeline can run on a laptop with no credentials (KAVACH_MODE=local)."""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    # "local" -> filesystem + JSON DB + mock brain + mock TTS
    # "aws"   -> S3 + DynamoDB + Bedrock + Polly (each piece can still be overridden)
    MODE = _env("KAVACH_MODE", "local").lower()

    AWS_REGION = _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1"))

    STORAGE = _env("KAVACH_STORAGE", "s3" if MODE == "aws" else "local")   # s3 | local
    DB = _env("KAVACH_DB", "dynamodb" if MODE == "aws" else "local")       # dynamodb | local
    BRAIN = _env("KAVACH_BRAIN", "converse" if MODE == "aws" else "mock")  # converse | bedrock | mock
    TTS = _env("KAVACH_TTS", "polly" if MODE == "aws" else "mock")         # polly | mock

    S3_BUCKET = _env("KAVACH_S3_BUCKET", "kavach")
    S3_PREFIX = _env("KAVACH_S3_PREFIX", "kavach")
    DDB_TABLE_PREFIX = _env("KAVACH_DDB_PREFIX", "kavach")
    DDB_TABLE_DOCUMENTS = f"{DDB_TABLE_PREFIX}_documents"
    DDB_TABLE_TOPICS = f"{DDB_TABLE_PREFIX}_topics"
    DDB_TABLE_REELS = f"{DDB_TABLE_PREFIX}_reels"

    # Bedrock model / inference-profile id. Converse: "global.anthropic.claude-opus-4-6-v1",
    # "global.anthropic.claude-sonnet-4-6"; Mantle client: "anthropic.claude-opus-5".
    BEDROCK_MODEL = _env("KAVACH_BEDROCK_MODEL", "global.anthropic.claude-opus-4-6-v1")
    BRAIN_EFFORT = _env("KAVACH_BRAIN_EFFORT", "high")
    BRAIN_MAX_TOKENS = int(_env("KAVACH_BRAIN_MAX_TOKENS", "32000"))

    POLLY_VOICE = _env("KAVACH_POLLY_VOICE", "Matthew")
    POLLY_ENGINE = _env("KAVACH_POLLY_ENGINE", "neural")

    DATA_DIR = Path(_env("KAVACH_DATA_DIR", str(BACKEND_DIR / "data")))
    WORK_DIR = Path(_env("KAVACH_WORK_DIR", str(BACKEND_DIR / "data" / "work")))

    VIDEO_WIDTH = int(_env("KAVACH_VIDEO_WIDTH", "720"))     # portrait 9:16 shorts
    VIDEO_HEIGHT = int(_env("KAVACH_VIDEO_HEIGHT", "1280"))
    VIDEO_FPS = int(_env("KAVACH_VIDEO_FPS", "24"))
    FONT_PATH = _env("KAVACH_FONT", "")

    MAX_PAGES = int(_env("KAVACH_MAX_PAGES", "60"))
    MAX_UPLOAD_MB = int(_env("KAVACH_MAX_UPLOAD_MB", "40"))
    # cap on characters of PDF text sent to the brain in a single call
    MAX_DOC_CHARS = int(_env("KAVACH_MAX_DOC_CHARS", "260000"))

    CORS_ORIGINS = [o for o in _env("KAVACH_CORS_ORIGINS", "*").split(",") if o]
    # public base URL used when building media links in local storage mode
    PUBLIC_BASE_URL = _env("KAVACH_PUBLIC_BASE_URL", "http://localhost:8000")
    WORKERS = int(_env("KAVACH_WORKERS", "2"))


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.WORK_DIR.mkdir(parents=True, exist_ok=True)

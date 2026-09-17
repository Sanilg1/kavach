"""Narration: Amazon Polly, with offline fallbacks.

    polly  - Amazon Polly (one consistent neural voice)
    sapi   - Windows built-in speech (System.Speech) - local dev only
    mock   - timed silence (word-count based) so the pipeline still assembles video
    auto   - sapi if it works on this machine, else mock
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

from ..config import settings
from ..video.ffmpeg import ffmpeg_exe, media_duration

log = logging.getLogger("kavach.tts")

_POLLY_MAX_CHARS = 2800


def estimate_seconds(text: str) -> float:
    words = len(re.findall(r"\S+", text))
    return max(2.0, words / 2.7 + 0.5)


def _split_for_polly(text: str) -> list[str]:
    if len(text) <= _POLLY_MAX_CHARS:
        return [text]
    chunks, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if len(cur) + len(s) + 1 > _POLLY_MAX_CHARS and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks


class PollyTTS:
    def __init__(self):
        import boto3

        self.client = boto3.client("polly", region_name=settings.AWS_REGION)

    def synthesize(self, text: str, out_mp3: Path) -> float:
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        with open(out_mp3, "wb") as f:
            for chunk in _split_for_polly(text):
                resp = self.client.synthesize_speech(
                    Text=chunk, OutputFormat="mp3", VoiceId=settings.POLLY_VOICE,
                    Engine=settings.POLLY_ENGINE, LanguageCode="en-US",
                )
                f.write(resp["AudioStream"].read())
        return media_duration(out_mp3)


class SapiTTS:
    """Windows System.Speech via PowerShell; WAV -> MP3 through ffmpeg."""

    def __init__(self):
        if not sys.platform.startswith("win"):
            raise RuntimeError("SAPI is Windows-only")
        # probe once so 'auto' can fall back cleanly
        probe = settings.WORK_DIR / "_sapi_probe.mp3"
        self.synthesize("Kavach", probe)
        probe.unlink(missing_ok=True)

    def synthesize(self, text: str, out_mp3: Path) -> float:
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        wav = out_mp3.with_suffix(".wav")
        txt = out_mp3.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 0; "
            f"$s.SetOutputToWaveFile('{wav}'); "
            f"$t = Get-Content -Raw -Encoding UTF8 '{txt}'; $s.Speak($t); $s.Dispose();"
        )
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True,
                       capture_output=True, timeout=120)
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame",
                        "-q:a", "4", str(out_mp3)], check=True, capture_output=True)
        wav.unlink(missing_ok=True)
        txt.unlink(missing_ok=True)
        return media_duration(out_mp3)


class MockTTS:
    def synthesize(self, text: str, out_mp3: Path) -> float:
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        dur = estimate_seconds(text)
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                        "-t", f"{dur:.2f}", "-codec:a", "libmp3lame", "-q:a", "9", str(out_mp3)],
                       check=True, capture_output=True)
        return dur


def _build():
    kind = settings.TTS
    if kind == "polly":
        return PollyTTS()
    if kind == "sapi":
        return SapiTTS()
    if kind in ("auto", "mock") and kind == "auto":
        try:
            return SapiTTS()
        except Exception as e:  # noqa: BLE001
            log.info("SAPI unavailable (%s); using silent mock narration", e)
    return MockTTS()


tts = _build()

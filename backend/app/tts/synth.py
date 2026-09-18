"""Narration: Amazon Polly, with offline fallbacks.

    polly  - Amazon Polly (one consistent neural voice) + word-level speech marks
    sapi   - Windows built-in speech (System.Speech) - local dev only
    mock   - timed silence (word-count based) so the pipeline still assembles video
    auto   - sapi if it works on this machine, else mock

Every backend returns a Narration: the MP3 length plus (time, word) marks that the
renderer uses for synced captions. Backends without real marks estimate them.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from ..video.ffmpeg import ffmpeg_exe, media_duration

log = logging.getLogger("kavach.tts")

_POLLY_MAX_CHARS = 2800


@dataclass
class Narration:
    duration: float                                   # seconds of audio
    words: list[tuple[float, str]] = field(default_factory=list)   # (start time s, word)


def estimate_seconds(text: str) -> float:
    words = len(re.findall(r"\S+", text))
    return max(2.0, words / 2.7 + 0.5)


def estimate_words(text: str, duration: float, lead: float = 0.15) -> list[tuple[float, str]]:
    """Spread words over the audio proportionally to their length (fallback marks)."""
    words = re.findall(r"\S+", text)
    if not words:
        return []
    weights = [len(w) + 2 + (3 if w[-1] in ".!?;:" else 0) for w in words]
    total = sum(weights)
    usable = max(0.5, duration - lead - 0.3)
    out, t = [], lead
    for w, wt in zip(words, weights):
        out.append((round(t, 3), w))
        t += usable * wt / total
    return out


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

    def _speech(self, text: str, **kw):
        return self.client.synthesize_speech(
            Text=text, VoiceId=settings.POLLY_VOICE, Engine=settings.POLLY_ENGINE, LanguageCode="en-US", **kw
        )

    def synthesize(self, text: str, out_mp3: Path) -> Narration:
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        words: list[tuple[float, str]] = []
        offset = 0.0
        chunks = _split_for_polly(text)
        with open(out_mp3, "wb") as f:
            for i, chunk in enumerate(chunks):
                audio = self._speech(chunk, OutputFormat="mp3")["AudioStream"].read()
                f.write(audio)
                try:
                    marks = self._speech(chunk, OutputFormat="json", SpeechMarkTypes=["word"])["AudioStream"].read()
                    raw = chunk.encode("utf-8")
                    for line in marks.decode("utf-8").splitlines():
                        if not line.strip():
                            continue
                        m = json.loads(line)
                        if m.get("type") != "word":
                            continue
                        # marks carry byte offsets into the input; take the original token
                        # plus trailing punctuation so captions keep sentence boundaries
                        end = m.get("end", 0)
                        while end < len(raw) and raw[end : end + 1] in b".,;:!?)\"'":
                            end += 1
                        token = raw[m.get("start", 0) : end].decode("utf-8", "ignore").strip() or m["value"]
                        words.append((round(offset + m["time"] / 1000.0, 3), token))
                except Exception as e:  # noqa: BLE001 - captions degrade to estimates
                    log.warning("speech marks unavailable: %s", e)
                if len(chunks) > 1:
                    tmp = out_mp3.with_name(f"{out_mp3.stem}_chunk{i}.mp3")
                    tmp.write_bytes(audio)
                    offset += media_duration(tmp)
                    tmp.unlink(missing_ok=True)
        duration = media_duration(out_mp3)
        if not words:
            words = estimate_words(text, duration)
        return Narration(duration=duration, words=words)


class SapiTTS:
    """Windows System.Speech via PowerShell; WAV -> MP3 through ffmpeg."""

    def __init__(self):
        if not sys.platform.startswith("win"):
            raise RuntimeError("SAPI is Windows-only")
        # probe once so 'auto' can fall back cleanly
        probe = settings.WORK_DIR / "_sapi_probe.mp3"
        self.synthesize("Kavach", probe)
        probe.unlink(missing_ok=True)

    def synthesize(self, text: str, out_mp3: Path) -> Narration:
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
        duration = media_duration(out_mp3)
        return Narration(duration=duration, words=estimate_words(text, duration))


class MockTTS:
    def synthesize(self, text: str, out_mp3: Path) -> Narration:
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        dur = estimate_seconds(text)
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                        "-t", f"{dur:.2f}", "-codec:a", "libmp3lame", "-q:a", "9", str(out_mp3)],
                       check=True, capture_output=True)
        return Narration(duration=dur, words=estimate_words(text, dur))


def _build():
    kind = settings.TTS
    if kind == "polly":
        return PollyTTS()
    if kind == "sapi":
        return SapiTTS()
    if kind == "auto":
        try:
            return SapiTTS()
        except Exception as e:  # noqa: BLE001
            log.info("SAPI unavailable (%s); using silent mock narration", e)
    return MockTTS()


tts = _build()

"""FFmpeg helpers: binary discovery, media duration, audio concat, frame piping."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable

_FFMPEG: str | None = None


def ffmpeg_exe() -> str:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    exe = shutil.which("ffmpeg")
    if not exe:
        import imageio_ffmpeg  # bundled static binary

        exe = imageio_ffmpeg.get_ffmpeg_exe()
    _FFMPEG = exe
    return exe


def media_duration(path: Path) -> float:
    """Duration in seconds via mutagen (mp3) with an ffmpeg fallback."""
    try:
        from mutagen.mp3 import MP3

        return float(MP3(str(path)).info.length)
    except Exception:
        pass
    r = subprocess.run([ffmpeg_exe(), "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    import re

    times = re.findall(r"time=(\d+):(\d+):(\d+\.?\d*)", r.stderr)
    if not times:
        raise RuntimeError(f"Could not determine duration of {path}")
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def concat_audio(segments: list[tuple[Path, float]], out_path: Path) -> None:
    """segments: (mp3 path, seconds of silence to append after it)."""
    args = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    for p, _ in segments:
        args += ["-i", str(p)]
    filt = []
    for i, (_, pad) in enumerate(segments):
        filt.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=mono,apad=pad_dur={max(pad, 0):.3f}[a{i}]")
    filt.append("".join(f"[a{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[out]")
    args += ["-filter_complex", ";".join(filt), "-map", "[out]", "-codec:a", "aac", "-b:a", "128k", str(out_path)]
    subprocess.run(args, check=True, capture_output=True)


def encode_video(frames: Iterable[bytes], width: int, height: int, fps: int, audio_path: Path, out_path: Path) -> None:
    """Pipe raw RGB frames into ffmpeg and mux with the narration track."""
    args = [
        ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for fr in frames:
            proc.stdin.write(fr)
    finally:
        proc.stdin.close()
        _, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='ignore')[-2000:]}")

"""Render the last frame of every scene of teaching plans into a contact sheet, for
eyeballing layout. Usage:

    python scripts/render_check.py plan.json [plan2.json ...] --out sheet.png
    python scripts/render_check.py --live <api-base> <document_id> --out sheet.png

A plan file may be a TeachingPlan, a single part, or the /reels/{id}/plan response.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from app.brain.planner import normalise_plan  # noqa: E402
from app.render.fonts import get_font  # noqa: E402
from app.render.renderer import Renderer  # noqa: E402
from app.tts.synth import estimate_seconds, estimate_words  # noqa: E402


def fetch_json(url: str, tries: int = 5):
    import time

    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(url, timeout=30))
        except Exception:  # noqa: BLE001 - flaky networks
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def load_parts(raw) -> list:
    if "part" in raw and isinstance(raw["part"], dict):
        raw = {"parts": [raw["part"]]}
    elif "scenes" in raw:
        raw = {"parts": [raw]}
    plan = normalise_plan(raw, {"name": raw.get("topic", "Topic"), "topic_id": "t", "source_pages": [1]}, 999)
    return plan.parts


def render_frames(part, width=720, height=1280) -> list[Image.Image]:
    durs = [max(3.0, estimate_seconds(s.narration)) + 0.7 for s in part.scenes]
    words = [estimate_words(s.narration, d - 0.7) for s, d in zip(part.scenes, durs)]
    r = Renderer(width, height, 24, footer="Source: PDF")
    r.build(part, durs, words)
    return [r.frame_at(st.end - 0.4) for st in r.scenes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plans", nargs="*")
    ap.add_argument("--live", nargs=2, metavar=("API", "DOC"))
    ap.add_argument("--out", default="data/work/sheet.png")
    ap.add_argument("--scale", type=float, default=0.33)
    args = ap.parse_args()

    rows: list[tuple[str, list[Image.Image]]] = []
    sources = []
    if args.live:
        api, doc = args.live
        cache = Path(f"data/work/plans_{doc}")
        cache.mkdir(parents=True, exist_ok=True)
        reels = fetch_json(f"{api}/documents/{doc}/reels")["reels"]
        for r in reels:
            if r.get("status") == "COMPLETED":
                f = cache / f"{r['reel_id']}.json"
                if not f.exists():
                    f.write_text(json.dumps(fetch_json(f"{api}/reels/{r['reel_id']}/plan")), encoding="utf-8")
                sources.append((r["title"], json.loads(f.read_text(encoding="utf-8"))))
    for f in args.plans:
        sources.append((Path(f).stem, json.load(open(f, encoding="utf-8"))))
    for name, raw in sources:
        for part in load_parts(raw):
            rows.append((f"{name} | {part.title}", render_frames(part)))

    w = int(720 * args.scale)
    h = int(1280 * args.scale)
    cols = max(len(fr) for _, fr in rows)
    sheet = Image.new("RGB", (cols * (w + 6) + 6, len(rows) * (h + 28) + 6), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    font = get_font(16)
    for ri, (label, frames) in enumerate(rows):
        y = 6 + ri * (h + 28)
        d.text((8, y), label[:110], fill=(255, 255, 255), font=font)
        for ci, fr in enumerate(frames):
            sheet.paste(fr.resize((w, h)), (6 + ci * (w + 6), y + 22))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.out)
    print(f"{len(rows)} parts -> {args.out}")


if __name__ == "__main__":
    main()

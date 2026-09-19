"""Deterministic whiteboard renderer.

Input: a PlanPart (scenes of primitives + narration) and the measured duration of
each scene's narration. Output: RGB frames. The Brain says WHAT to show; this module
decides HOW: layout defaults, hand-drawn strokes, animation timing.

Primitives: TEXT BOX CIRCLE LINE ARROW DIAGRAM EQUATION TABLE HIGHLIGHT IMAGE FLOW TIMELINE
Animations: DRAW WRITE FADE_IN FADE_OUT MOVE HIGHLIGHT ARROW_FLOW SEQUENTIAL_REVEAL
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from PIL import Image, ImageDraw

from ..models import Element, PlanPart
from .fonts import font_for, get_font

BG = (255, 255, 253)
COLORS = {
    "ink": (31, 41, 51), "blue": (29, 78, 216), "red": (220, 38, 38), "green": (21, 128, 61),
    "orange": (234, 88, 12), "purple": (124, 58, 237), "grey": (107, 114, 128), "gray": (107, 114, 128),
}
TINTS = {
    "ink": (243, 244, 246), "blue": (219, 234, 254), "red": (254, 226, 226), "green": (220, 252, 231),
    "orange": (255, 237, 213), "purple": (237, 233, 254), "grey": (243, 244, 246), "gray": (243, 244, 246),
}
HIGHLIGHT_RGB = (253, 224, 71)
WIPE = 0.35  # seconds to fade the old board out on a "clear" scene

Point = tuple[float, float]
BBox = tuple[float, float, float, float]


def _color(name: str) -> tuple[int, int, int]:
    return COLORS.get((name or "ink").lower(), COLORS["ink"])


def _tint(name: str) -> tuple[int, int, int]:
    return TINTS.get((name or "ink").lower(), TINTS["ink"])


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def _ease(p: float) -> float:
    p = _clamp(p)
    return p * p * (3 - 2 * p)


@dataclass
class Timed:
    el: Element
    scene_idx: int
    start: float          # absolute seconds
    dur: float
    order: int
    pos: Optional[Point] = None      # auto-assigned 0-100 position when missing
    bbox: Optional[BBox] = None      # final-state bbox in px (measured)


@dataclass
class SceneTiming:
    start: float
    end: float
    clear: bool
    elements: list[Timed] = field(default_factory=list)


@dataclass
class Caption:
    start: float
    end: float
    words: list[tuple[float, str]]     # absolute (time, word)


class Renderer:
    def __init__(self, width: int, height: int, fps: int,
                 page_image: Optional[Callable[[int], Optional[Image.Image]]] = None, footer: str = "",
                 captions: bool = True, progress_bar: bool = True):
        self.W, self.H, self.fps = width, height, fps
        self.captions_on = captions
        self.progress_bar = progress_bar
        self.captions: list[Caption] = []
        self.base = min(width, height)             # short side: 720 for both 720x1280 and 1280x720
        self.s = self.base / 720.0                 # scale factor for strokes, offsets, fonts
        self.page_image = page_image
        self.footer = footer
        self.scenes: list[SceneTiming] = []
        self.total = 0.0
        self._base: Optional[Image.Image] = None
        self._base_key: tuple = ()
        self._img_cache: dict[int, Image.Image] = {}
        self._all: dict[str, Timed] = {}

    # ------------------------------------------------------------------ units
    def px(self, x: float) -> float:
        return x / 100.0 * self.W

    def py(self, y: float) -> float:
        return y / 100.0 * self.H

    def font_px(self, size: str) -> int:
        return int({"title": 0.064, "large": 0.05, "normal": 0.038, "small": 0.03}.get(size, 0.038) * self.base)

    def stroke(self, k: float = 1.0) -> int:
        return max(2, int(round(3 * self.s * k)))

    # ------------------------------------------------------------------ timeline
    @staticmethod
    def _anim_duration(el: Element) -> float:
        t, a = el.type, el.animation
        n_steps = max(1, len(el.steps))
        if t in ("TEXT", "EQUATION") and a in ("WRITE", "SEQUENTIAL_REVEAL"):
            return min(2.4, 0.35 + 0.035 * len(el.text))
        if t == "FLOW":
            return 0.45 * n_steps + 0.3
        if t == "TIMELINE":
            return 0.4 * n_steps + 0.3
        if t == "TABLE":
            return 0.35 * max(1, len(el.rows)) + 0.3
        if t == "DIAGRAM":
            return 0.3 * (len(el.nodes) + len(el.edges)) + 0.3
        if a == "ARROW_FLOW":
            return 1.6
        if a == "HIGHLIGHT" or t == "HIGHLIGHT":
            return 1.6
        if a == "MOVE":
            return 1.0
        if a in ("FADE_IN", "FADE_OUT"):
            return 0.5
        if t in ("BOX", "CIRCLE"):
            return 0.8
        if t == "IMAGE":
            return 0.6
        return 0.6

    def _default_size(self, el: Element) -> tuple[float, float]:
        t = el.type
        if t == "BOX":
            return (el.w or 18, el.h or 12)
        if t == "CIRCLE":
            return (el.w or 14, el.w or 14)
        if t == "FLOW":
            return (el.w or 84, el.h or (12 if el.direction == "horizontal" else 10 * max(1, len(el.steps))))
        if t == "TIMELINE":
            return (el.w or 80, el.h or 16)
        if t == "TABLE":
            return (el.w or 70, el.h or 7 * max(1, len(el.rows)) + 2)
        if t == "IMAGE":
            return (el.w or 34, el.h or 42)
        if t in ("TEXT", "EQUATION"):
            bbox = self._measure_text(el, (50, 50))
            return ((bbox[2] - bbox[0]) / self.W * 100, (bbox[3] - bbox[1]) / self.H * 100 + 2)
        return (el.w or 20, el.h or 10)

    def build(self, part: PlanPart, scene_durations: list[float],
              scene_words: Optional[list[list[tuple[float, str]]]] = None) -> None:
        t0 = 0.0
        cursor_y = 22.0
        order = 0
        self.scenes = []
        self._all = {}
        for si, (scene, dur) in enumerate(zip(part.scenes, scene_durations)):
            st = SceneTiming(start=t0, end=t0 + dur, clear=scene.clear)
            if scene.clear:
                cursor_y = 22.0
            lead = (WIPE if (scene.clear and si > 0) else 0.0) + 0.25
            avail = max(0.5, dur - lead - 0.8)
            els = scene.elements
            durs = [self._anim_duration(e) for e in els]
            gap = 0.15
            need = sum(durs) + gap * max(0, len(els) - 1)
            scale = min(1.0, avail / need) if need > 0 else 1.0
            cur = t0 + lead
            for e, d in zip(els, durs):
                d = max(0.25, d * scale)
                timed = Timed(el=e, scene_idx=si, start=cur, dur=d, order=order)
                order += 1
                if e.x is None or e.y is None:
                    if e.type == "HIGHLIGHT":
                        pass
                    elif e.type == "DIAGRAM":
                        timed.pos = (50.0, 55.0)
                    else:
                        w, h = self._default_size(e)
                        if e.type == "TEXT" and e.size == "title" and cursor_y <= 22.0:
                            timed.pos = (50.0, 8.0)
                        else:
                            timed.pos = (50.0, min(92.0, cursor_y + h / 2))
                            cursor_y = min(95.0, cursor_y + h + 3)
                elif e.type != "HIGHLIGHT":
                    _, h = self._default_size(e)
                    cursor_y = max(cursor_y, min(95.0, e.y + h / 2 + 3))
                st.elements.append(timed)
                self._all[e.id] = timed
                cur += d + gap * scale
            self.scenes.append(st)
            t0 += dur
        self.total = t0
        self.captions = self._build_captions(scene_words or []) if self.captions_on else []
        # measure final bboxes (needed for arrows/highlights referencing ids)
        for st in self.scenes:
            for tm in st.elements:
                tm.bbox = self._measure(tm)

    # ------------------------------------------------------------------ geometry
    def _center(self, tm: Timed, p: float = 1.0) -> Point:
        el = tm.el
        x, y = (el.x, el.y) if (el.x is not None and el.y is not None) else (tm.pos or (50.0, 50.0))
        if el.animation == "MOVE" and el.to_x is not None and el.to_y is not None:
            e = _ease(p)
            x, y = x + (el.to_x - x) * e, y + (el.to_y - y) * e
        return (self.px(x), self.py(y))

    def _measure_text(self, el: Element, center100: Point, max_w100: Optional[float] = None) -> BBox:
        font = font_for(self.font_px(el.size), el.text)
        lines = self._wrap(el.text, font, self.px(max_w100 or el.w or 86))
        lh = font.size * 1.25
        w = max((font.getlength(l) for l in lines), default=0)
        h = lh * len(lines)
        cx, cy = self.px(center100[0]), self.py(center100[1])
        pad = 8 * self.s if el.type == "EQUATION" else 0
        return (cx - w / 2 - pad, cy - h / 2 - pad, cx + w / 2 + pad, cy + h / 2 + pad)

    def _measure(self, tm: Timed) -> BBox:
        el = tm.el
        cx, cy = self._center(tm)
        if el.type in ("TEXT", "EQUATION"):
            c100 = (cx / self.W * 100, cy / self.H * 100)
            return self._measure_text(el, c100)
        if el.type == "HIGHLIGHT":
            tgt = self._all.get(el.target)
            return tgt.bbox if tgt and tgt.bbox else (cx - 10, cy - 10, cx + 10, cy + 10)
        if el.type in ("LINE", "ARROW"):
            p0, p1 = self._line_points(tm)
            return (min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1]))
        if el.type == "DIAGRAM":
            xs = [self.px(float(n.get("x", 50))) for n in el.nodes] or [cx]
            ys = [self.py(float(n.get("y", 50))) for n in el.nodes] or [cy]
            r = self.px(7)
            return (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)
        w, h = self._default_size(el)
        return (cx - self.px(w) / 2, cy - self.py(h) / 2, cx + self.px(w) / 2, cy + self.py(h) / 2)

    def _line_points(self, tm: Timed) -> tuple[Point, Point]:
        el = tm.el
        src = self._all.get(el.from_id) if el.from_id else None
        dst = self._all.get(el.to_id) if el.to_id else None
        if src and dst and src.bbox and dst.bbox:
            c0 = self._bbox_center(src.bbox)
            c1 = self._bbox_center(dst.bbox)
            return self._clip_to_rect(c1, c0, src.bbox), self._clip_to_rect(c0, c1, dst.bbox)
        if el.x is not None and el.y is not None and el.x2 is not None and el.y2 is not None:
            return (self.px(el.x), self.py(el.y)), (self.px(el.x2), self.py(el.y2))
        cx, cy = self._center(tm)
        w = self.px(el.w or 30)
        return (cx - w / 2, cy), (cx + w / 2, cy)

    @staticmethod
    def _bbox_center(b: BBox) -> Point:
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    @staticmethod
    def _clip_to_rect(outside: Point, center: Point, rect: BBox) -> Point:
        """Point where the segment center->outside leaves the rect (with a small margin)."""
        dx, dy = outside[0] - center[0], outside[1] - center[1]
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return center
        hw, hh = (rect[2] - rect[0]) / 2 + 6, (rect[3] - rect[1]) / 2 + 6
        tx = hw / abs(dx) if dx else float("inf")
        ty = hh / abs(dy) if dy else float("inf")
        t = min(tx, ty)
        return (center[0] + dx * t, center[1] + dy * t)

    def _wrap(self, text: str, font, max_w: float) -> list[str]:
        lines: list[str] = []
        for para in (text or "").split("\n"):
            words = para.split()
            cur = ""
            for w in words:
                trial = f"{cur} {w}".strip()
                if font.getlength(trial) <= max_w or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            lines.append(cur)
        return lines or [""]

    # ------------------------------------------------------------------ sketchy strokes
    def _wobble(self, p0: Point, p1: Point, seed: int, amp: float = 1.3) -> list[Point]:
        rnd = random.Random(seed)
        length = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        n = max(2, int(length / (14 * self.s)))
        if length < 1e-6:
            return [p0, p1]
        nx, ny = -(p1[1] - p0[1]) / length, (p1[0] - p0[0]) / length
        pts = []
        drift = 0.0
        for i in range(n + 1):
            t = i / n
            drift = drift * 0.6 + rnd.uniform(-1, 1) * amp * self.s
            edge = 1.0 if 0 < i < n else 0.0
            pts.append((p0[0] + (p1[0] - p0[0]) * t + nx * drift * edge, p0[1] + (p1[1] - p0[1]) * t + ny * drift * edge))
        return pts

    def _polyline(self, draw: ImageDraw.ImageDraw, pts: list[Point], color, width: int, progress: float) -> None:
        if progress <= 0 or len(pts) < 2:
            return
        total = len(pts) - 1
        k = progress * total
        full = int(k)
        seg = pts[: full + 1]
        if full < total:
            f = k - full
            a, b = pts[full], pts[full + 1]
            seg.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        if len(seg) >= 2:
            draw.line(seg, fill=color, width=width, joint="curve")

    def _sketch_path(self, draw, points: list[Point], color, width: int, progress: float, seed: int, closed=False) -> None:
        segs = list(zip(points, points[1:] + ([points[0]] if closed else [])))
        n = len(segs)
        for i, (a, b) in enumerate(segs):
            local = _clamp(progress * n - i)
            if local <= 0:
                break
            self._polyline(draw, self._wobble(a, b, seed + i), color, width, local)

    def _sketch_rect(self, draw, b: BBox, color, width: int, progress: float, seed: int, fill=None) -> None:
        if fill is not None and progress > 0.5:
            draw.rectangle([b[0] + 2, b[1] + 2, b[2] - 2, b[3] - 2], fill=fill)
        self._sketch_path(draw, [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])], color, width, progress, seed, closed=True)

    def _sketch_ellipse(self, draw, b: BBox, color, width: int, progress: float, seed: int, fill=None) -> None:
        cx, cy = self._bbox_center(b)
        rx, ry = (b[2] - b[0]) / 2, (b[3] - b[1]) / 2
        rnd = random.Random(seed)
        pts = []
        n = 48
        for i in range(n + 1):
            a = -math.pi / 2 + 2 * math.pi * i / n
            j = rnd.uniform(-1.2, 1.2) * self.s
            pts.append((cx + (rx + j) * math.cos(a), cy + (ry + j) * math.sin(a)))
        if fill is not None and progress > 0.5:
            draw.ellipse([b[0] + 2, b[1] + 2, b[2] - 2, b[3] - 2], fill=fill)
        self._polyline(draw, pts, color, width, progress)

    def _arrowhead(self, draw, p0: Point, p1: Point, color, size: float) -> None:
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        left = (p1[0] - size * math.cos(ang - 0.45), p1[1] - size * math.sin(ang - 0.45))
        right = (p1[0] - size * math.cos(ang + 0.45), p1[1] - size * math.sin(ang + 0.45))
        draw.polygon([p1, left, right], fill=color)

    def _text_lines(self, draw, el_size: str, text: str, center: Point, color, reveal: float = 1.0,
                    max_w: Optional[float] = None, anchor_top: bool = False) -> BBox:
        font = font_for(self.font_px(el_size), text)
        lines = self._wrap(text, font, max_w or self.px(86))
        lh = font.size * 1.25
        total_chars = sum(len(l) for l in lines) or 1
        shown = int(round(reveal * total_chars)) if reveal < 1 else total_chars
        h = lh * len(lines)
        y = center[1] if anchor_top else center[1] - h / 2 + lh / 2
        maxw = 0.0
        for line in lines:
            maxw = max(maxw, font.getlength(line))
            if shown <= 0:
                break
            part = line[:shown]
            shown -= len(line)
            # keep the line's final centre so text does not jump while writing
            x0 = center[0] - font.getlength(line) / 2
            draw.text((x0, y), part, font=font, fill=color, anchor="lm")
            y += lh
        return (center[0] - maxw / 2, center[1] - h / 2, center[0] + maxw / 2, center[1] + h / 2)

    def _label_bg(self, draw, text: str, center: Point, size: str, color, pad: float = 5) -> None:
        font = font_for(self.font_px(size), text)
        w = font.getlength(text)
        h = font.size * 1.1
        draw.rounded_rectangle([center[0] - w / 2 - pad, center[1] - h / 2 - 2, center[0] + w / 2 + pad, center[1] + h / 2 + 2],
                               radius=6 * self.s, fill=BG)
        draw.text(center, text, font=font, fill=color, anchor="mm")

    # ------------------------------------------------------------------ element drawing
    def _draw_element(self, img: Image.Image, tm: Timed, p: float, alpha: float, t: float) -> None:
        el = tm.el
        if alpha <= 0.01:
            return
        if alpha < 0.99 or el.type == "HIGHLIGHT" or el.animation == "HIGHLIGHT":
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            self._draw_shape(draw, tm, p, t, layer)
            if el.animation == "HIGHLIGHT" and el.type != "HIGHLIGHT":
                self._draw_highlight(draw, tm.bbox, p, t - tm.start)
            mask = layer.split()[3]
            if alpha < 0.99:
                mask = mask.point(lambda a: int(a * alpha))
            img.paste(layer, (0, 0), mask)
        else:
            self._draw_shape(ImageDraw.Draw(img), tm, p, t, img)

    def _draw_highlight(self, draw, bbox: Optional[BBox], p: float, local_t: float) -> None:
        if not bbox:
            return
        pulse = 0.38 + 0.12 * math.sin(local_t * 6.0) if p < 1 else 0.36
        a = int(255 * pulse * _ease(min(1.0, p * 3)))
        m = 8 * self.s
        draw.rounded_rectangle([bbox[0] - m, bbox[1] - m * 0.6, bbox[2] + m, bbox[3] + m * 0.6],
                               radius=8 * self.s, fill=HIGHLIGHT_RGB + (a,))

    def _draw_shape(self, draw: ImageDraw.ImageDraw, tm: Timed, p: float, t: float, img: Image.Image) -> None:
        el = tm.el
        typ, anim = el.type, el.animation
        color = _color(el.color)
        seed = zlib.crc32(el.id.encode()) & 0xFFFF
        e = _ease(p)
        draw_p = e if anim in ("DRAW", "WRITE", "SEQUENTIAL_REVEAL", "ARROW_FLOW") else 1.0
        cx, cy = self._center(tm, p if anim == "MOVE" else 1.0)

        if typ == "HIGHLIGHT":
            tgt = self._all.get(el.target)
            self._draw_highlight(draw, tgt.bbox if tgt else tm.bbox, p, t - tm.start)
            return

        if typ in ("TEXT", "EQUATION"):
            reveal = draw_p if anim in ("WRITE", "SEQUENTIAL_REVEAL") else 1.0
            if typ == "EQUATION":
                b = self._measure_text(el, (cx / self.W * 100, cy / self.H * 100))
                draw.rounded_rectangle(b, radius=8 * self.s, fill=_tint(el.color or "purple"))
            self._text_lines(draw, el.size, el.text, (cx, cy), color, reveal, self.px(el.w) if el.w else None)
            return

        if typ in ("BOX", "CIRCLE"):
            w, h = self._default_size(el)
            b = (cx - self.px(w) / 2, cy - self.py(h) / 2, cx + self.px(w) / 2, cy + self.py(h) / 2)
            fill = _tint(el.color)
            if typ == "BOX":
                self._sketch_rect(draw, b, color, self.stroke(), draw_p, seed, fill=fill)
            else:
                self._sketch_ellipse(draw, b, color, self.stroke(), draw_p, seed, fill=fill)
            if el.label and draw_p > 0.5:
                self._text_lines(draw, "normal" if len(el.label) < 18 else "small", el.label, (cx, cy), color,
                                 max_w=self.px(w) - 10)
            return

        if typ in ("LINE", "ARROW"):
            p0, p1 = self._line_points(tm)
            line_p = min(1.0, draw_p / 0.3) if anim == "ARROW_FLOW" else draw_p
            self._polyline(draw, self._wobble(p0, p1, seed), color, self.stroke(), line_p)
            if typ == "ARROW" and line_p >= 0.97:
                self._arrowhead(draw, p0, p1, color, 14 * self.s)
            if el.label and line_p > 0.6:
                mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
                ln = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) or 1
                nx, ny = -(p1[1] - p0[1]) / ln, (p1[0] - p0[0]) / ln
                off = 16 * self.s
                self._label_bg(draw, el.label, (mx + nx * off, my + ny * off), "small", color)
            if anim == "ARROW_FLOW" and 0.3 < draw_p < 1.0:
                f = (draw_p - 0.3) / 0.7
                dx, dy = p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f
                r = 7 * self.s
                draw.ellipse([dx - r, dy - r, dx + r, dy + r], fill=color, outline=BG, width=2)
            return

        if typ == "FLOW":
            self._draw_flow(draw, tm, cx, cy, color, draw_p, seed)
            return
        if typ == "TIMELINE":
            self._draw_timeline(draw, tm, cx, cy, color, draw_p, seed)
            return
        if typ == "TABLE":
            self._draw_table(draw, tm, cx, cy, color, draw_p, seed)
            return
        if typ == "DIAGRAM":
            self._draw_diagram(draw, tm, color, draw_p, seed)
            return
        if typ == "IMAGE":
            self._draw_image(draw, img, tm, cx, cy, color, seed)
            return

    def _draw_flow(self, draw, tm, cx, cy, color, p, seed) -> None:
        el = tm.el
        steps = el.steps or [el.text or el.label or "..."]
        n = len(steps)
        w, h = self._default_size(el)
        W, H = self.px(w), self.py(h)
        horiz = el.direction == "horizontal"
        gap = (20 if horiz else 14) * self.s
        if horiz:
            bw = (W - gap * (n - 1)) / n
            bh = H
        else:
            bw = min(W, self.px(40))
            bh = (H - gap * (n - 1)) / n
        x0, y0 = cx - W / 2, cy - H / 2
        prev = None
        for i, label in enumerate(steps):
            local = _clamp(p * n - i)
            if local <= 0:
                break
            if horiz:
                b = (x0 + i * (bw + gap), cy - bh / 2, x0 + i * (bw + gap) + bw, cy + bh / 2)
            else:
                b = (cx - bw / 2, y0 + i * (bh + gap), cx + bw / 2, y0 + i * (bh + gap) + bh)
            if prev is not None:
                a0 = (prev[2], (prev[1] + prev[3]) / 2) if horiz else ((prev[0] + prev[2]) / 2, prev[3])
                a1 = (b[0], (b[1] + b[3]) / 2) if horiz else ((b[0] + b[2]) / 2, b[1])
                ap = _clamp(local * 2)
                self._polyline(draw, self._wobble(a0, a1, seed + 100 + i), color, self.stroke(), ap)
                if ap >= 0.97:
                    self._arrowhead(draw, a0, a1, color, 11 * self.s)
            bp = _clamp(local * 2 - 1) if prev is not None else local
            self._sketch_rect(draw, b, color, self.stroke(0.9), bp, seed + i, fill=_tint(el.color))
            if bp > 0.5:
                self._text_lines(draw, "small" if len(label) > 14 else "normal", label, self._bbox_center(b), color, max_w=bw - 8)
            prev = b

    def _draw_timeline(self, draw, tm, cx, cy, color, p, seed) -> None:
        el = tm.el
        steps = el.steps or ["start", "end"]
        n = len(steps)
        w, _ = self._default_size(el)
        W = self.px(w)
        x0, x1 = cx - W / 2, cx + W / 2
        self._polyline(draw, self._wobble((x0, cy), (x1, cy), seed), color, self.stroke(), min(1.0, p * n / max(1, n - 0.5)))
        for i, label in enumerate(steps):
            local = _clamp(p * n - i)
            if local <= 0:
                break
            x = x0 + (x1 - x0) * (i / max(1, n - 1))
            tick = 10 * self.s
            draw.line([(x, cy - tick), (x, cy + tick)], fill=color, width=self.stroke())
            r = 5 * self.s
            draw.ellipse([x - r, cy - r, x + r, cy + r], fill=color)
            above = i % 2 == 0
            self._text_lines(draw, "small", label, (x, cy + (-26 if above else 26) * self.s), color, max_w=W / n + 20)

    def _draw_table(self, draw, tm, cx, cy, color, p, seed) -> None:
        el = tm.el
        rows = el.rows or [["", ""]]
        ncol = max(len(r) for r in rows)
        nrow = len(rows)
        w, h = self._default_size(el)
        W, H = self.px(w), self.py(h)
        x0, y0 = cx - W / 2, cy - H / 2
        cw, rh = W / ncol, H / nrow
        shown = int(math.ceil(p * nrow)) if p < 1 else nrow
        if shown <= 0:
            return
        # header shading + outer frame grows with rows
        draw.rectangle([x0 + 2, y0 + 2, x0 + W - 2, y0 + rh - 2], fill=_tint(el.color or "blue"))
        self._sketch_rect(draw, (x0, y0, x0 + W, y0 + rh * shown), color, self.stroke(0.9), 1.0, seed)
        for c in range(1, ncol):
            self._polyline(draw, self._wobble((x0 + cw * c, y0), (x0 + cw * c, y0 + rh * shown), seed + c), color, self.stroke(0.6), 1.0)
        for r in range(1, shown):
            self._polyline(draw, self._wobble((x0, y0 + rh * r), (x0 + W, y0 + rh * r), seed + 50 + r), color, self.stroke(0.6), 1.0)
        for r in range(shown):
            for c, cell in enumerate(rows[r]):
                self._text_lines(draw, "small", cell, (x0 + cw * c + cw / 2, y0 + rh * r + rh / 2),
                                 color if r else _color(el.color or "blue"), max_w=cw - 10)

    def _draw_diagram(self, draw, tm, color, p, seed) -> None:
        el = tm.el
        nodes = el.nodes
        if not nodes:
            return
        # auto layout on a circle when positions are missing
        cx, cy = self._center(tm)
        for i, n in enumerate(nodes):
            if n.get("x") is None or n.get("y") is None:
                a = -math.pi / 2 + 2 * math.pi * i / len(nodes)
                n["x"], n["y"] = 50 + 28 * math.cos(a), 55 + 24 * math.sin(a)
        boxes: dict[str, BBox] = {}
        total = len(nodes) + len(el.edges)
        font_small = self.font_px("small")
        for i, n in enumerate(nodes):
            label = str(n.get("label", n.get("id", "")))
            nw = max(self.px(11), font_for(font_small, label).getlength(label) + 24 * self.s)
            nh = self.px(6.5)
            x, y = self.px(float(n["x"])), self.py(float(n["y"]))
            b = (x - nw / 2, y - nh / 2, x + nw / 2, y + nh / 2)
            boxes[str(n.get("id"))] = b
            local = _clamp(p * total - i)
            if local <= 0:
                continue
            ncolor = _color(str(n.get("color", el.color)))
            if n.get("shape") == "circle":
                self._sketch_ellipse(draw, b, ncolor, self.stroke(0.9), local, seed + i, fill=_tint(str(n.get("color", el.color))))
            else:
                self._sketch_rect(draw, b, ncolor, self.stroke(0.9), local, seed + i, fill=_tint(str(n.get("color", el.color))))
            if local > 0.5:
                self._text_lines(draw, "small", label, (x, y), ncolor, max_w=nw - 8)
        for j, e in enumerate(el.edges):
            local = _clamp(p * total - len(nodes) - j)
            if local <= 0:
                break
            a, b = boxes.get(str(e.get("from"))), boxes.get(str(e.get("to")))
            if not a or not b:
                continue
            c0, c1 = self._bbox_center(a), self._bbox_center(b)
            p0, p1 = self._clip_to_rect(c1, c0, a), self._clip_to_rect(c0, c1, b)
            ecolor = _color(str(e.get("color", el.color)))
            self._polyline(draw, self._wobble(p0, p1, seed + 200 + j), ecolor, self.stroke(0.9), local)
            if local >= 0.97:
                self._arrowhead(draw, p0, p1, ecolor, 12 * self.s)
                if e.get("label"):
                    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
                    self._label_bg(draw, str(e["label"]), (mx, my), "small", ecolor)

    def _draw_image(self, draw, img: Image.Image, tm, cx, cy, color, seed) -> None:
        el = tm.el
        w, h = self._default_size(el)
        W, H = self.px(w), self.py(h)
        b = (cx - W / 2, cy - H / 2, cx + W / 2, cy + H / 2)
        page = el.page
        pil = None
        if page and self.page_image:
            if page not in self._img_cache:
                try:
                    self._img_cache[page] = self.page_image(page)
                except Exception:
                    self._img_cache[page] = None
            pil = self._img_cache.get(page)
        if pil is not None:
            thumb = pil.copy()
            thumb.thumbnail((int(W) - 8, int(H) - 8))
            ox, oy = int(cx - thumb.width / 2), int(cy - thumb.height / 2)
            if img.mode == "RGBA":
                img.paste(thumb.convert("RGBA"), (ox, oy))
            else:
                img.paste(thumb.convert("RGB"), (ox, oy))
            b = (ox - 4, oy - 4, ox + thumb.width + 4, oy + thumb.height + 4)
        else:
            draw.rectangle(b, fill=_tint("grey"))
            self._text_lines(draw, "small", f"PDF page {page or '?'}", (cx, cy), _color("grey"))
        self._sketch_rect(draw, b, color, self.stroke(0.8), 1.0, seed)
        if page:
            self._label_bg(draw, f"p. {page}", (b[2] - 20 * self.s, b[3] + 12 * self.s), "small", _color("grey"))

    # ------------------------------------------------------------------ captions
    def _build_captions(self, scene_words: list[list[tuple[float, str]]]) -> list[Caption]:
        """Group each scene's (time, word) marks into short caption chunks."""
        caps: list[Caption] = []
        for st, words in zip(self.scenes, scene_words):
            chunk: list[tuple[float, str]] = []
            chunks: list[list[tuple[float, str]]] = []
            for t, w in words:
                chunk.append((st.start + t, w))
                ends_sentence = w[-1:] in ".!?"
                soft_break = w[-1:] in ",;:" and len(chunk) >= 4
                if len(chunk) >= 7 or ends_sentence or soft_break:
                    chunks.append(chunk)
                    chunk = []
            if chunk:
                chunks.append(chunk)
            for i, c in enumerate(chunks):
                end = chunks[i + 1][0][0] if i + 1 < len(chunks) else st.end - 0.15
                caps.append(Caption(start=c[0][0], end=max(c[0][0] + 0.4, end), words=c))
        return caps

    def _draw_caption(self, img: Image.Image, t: float) -> None:
        cap = next((c for c in self.captions if c.start - 0.12 <= t < c.end), None)
        if not cap:
            return
        draw = ImageDraw.Draw(img)
        font = font_for(self.font_px("normal"), " ".join(w for _, w in cap.words))
        space = font.getlength(" ")
        max_w = self.W * 0.86
        lines: list[list[tuple[float, str]]] = [[]]
        width = 0.0
        for tw in cap.words:
            wl = font.getlength(tw[1])
            if lines[-1] and width + space + wl > max_w:
                lines.append([tw])
                width = wl
            else:
                lines[-1].append(tw)
                width += (space if len(lines[-1]) > 1 else 0) + wl
        lh = font.size * 1.3
        pad_x, pad_y = 18 * self.s, 10 * self.s
        box_w = max(sum(font.getlength(w) for _, w in ln) + space * (len(ln) - 1) for ln in lines) + 2 * pad_x
        box_h = lh * len(lines) + 2 * pad_y
        cx, cy = self.W / 2, self.H - 96 * self.s
        draw.rounded_rectangle([cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2],
                               radius=12 * self.s, fill=(31, 41, 51))
        y = cy - box_h / 2 + pad_y + lh / 2
        spoken = [tw for tw in cap.words if tw[0] <= t]
        current = spoken[-1] if spoken else None
        for ln in lines:
            lw = sum(font.getlength(w) for _, w in ln) + space * (len(ln) - 1)
            x = cx - lw / 2
            for tw in ln:
                if tw is current:
                    color = HIGHLIGHT_RGB
                elif tw[0] <= t:
                    color = (255, 255, 255)
                else:
                    color = (156, 163, 175)
                draw.text((x, y), tw[1], font=font, fill=color, anchor="lm")
                x += font.getlength(tw[1]) + space
            y += lh

    def _draw_progress(self, img: Image.Image, t: float) -> None:
        if self.total <= 0:
            return
        draw = ImageDraw.Draw(img)
        h = max(3, int(4 * self.s))
        draw.rectangle([0, self.H - h, self.W, self.H], fill=(229, 231, 235))
        draw.rectangle([0, self.H - h, self.W * _clamp(t / self.total), self.H], fill=(31, 41, 51))

    # ------------------------------------------------------------------ frames
    def _blank(self) -> Image.Image:
        img = Image.new("RGB", (self.W, self.H), BG)
        if self.footer:
            d = ImageDraw.Draw(img)
            font = get_font(int(self.base * 0.026))
            d.text((self.W - 18 * self.s, self.H - 12 * self.s), self.footer, font=font, fill=_color("grey"), anchor="rd")
            d.text((18 * self.s, self.H - 12 * self.s), "Kavach", font=font, fill=_color("grey"), anchor="ld")
        return img

    def _scene_index(self, t: float) -> int:
        for i, st in enumerate(self.scenes):
            if t < st.end:
                return i
        return len(self.scenes) - 1

    def _segment_start(self, si: int) -> int:
        for i in range(si, -1, -1):
            if self.scenes[i].clear and i > 0:
                return i
        return 0

    def _state(self, tm: Timed, t: float) -> tuple[float, float]:
        """(progress, alpha) of an element at absolute time t."""
        p = _clamp((t - tm.start) / tm.dur) if tm.dur > 0 else 1.0
        a = tm.el.animation
        if a == "FADE_IN":
            return 1.0, _ease(p)
        if a == "FADE_OUT":
            return 1.0, 1.0 - _ease(p)
        return p, 1.0

    def frame_at(self, t: float) -> Image.Image:
        si = self._scene_index(t)
        seg0 = self._segment_start(si)
        cur_scene = self.scenes[si]

        settled: list[Timed] = []
        active: list[Timed] = []
        for i in range(seg0, si + 1):
            for tm in self.scenes[i].elements:
                if tm.start > t:
                    continue
                if t >= tm.start + tm.dur:
                    if tm.el.animation == "FADE_OUT":
                        continue          # gone for good
                    settled.append(tm)
                else:
                    active.append(tm)
        key = tuple(tm.order for tm in settled)
        if key != self._base_key or self._base is None:
            base = self._blank()
            for tm in settled:
                self._draw_element(base, tm, 1.0, 1.0, t)
            self._base, self._base_key = base, key
        img = self._base.copy()

        # wipe transition: previous segment fades out at the start of a clear scene
        if cur_scene.clear and si > 0 and t - cur_scene.start < WIPE:
            fade = 1.0 - (t - cur_scene.start) / WIPE
            prev_seg0 = self._segment_start(si - 1)
            old = self._blank()
            for i in range(prev_seg0, si):
                for tm in self.scenes[i].elements:
                    if tm.el.animation == "FADE_OUT":
                        continue
                    self._draw_element(old, tm, 1.0, 1.0, t)
            img = Image.blend(img, old, fade)

        for tm in active:
            p, alpha = self._state(tm, t)
            self._draw_element(img, tm, p, alpha, t)
        if self.captions:
            self._draw_caption(img, t)
        if self.progress_bar:
            self._draw_progress(img, t)
        return img

    def frames(self) -> Iterator[bytes]:
        n = int(round(self.total * self.fps))
        for i in range(n):
            yield self.frame_at(i / self.fps).tobytes()

    def poster(self) -> Image.Image:
        t = self.scenes[0].end - 0.05 if self.scenes else 0.0
        return self.frame_at(max(0.0, t))

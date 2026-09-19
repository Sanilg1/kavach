"""Handwriting-style font discovery. Order: KAVACH_FONT env, bundled assets/fonts,
common system handwriting fonts, then a plain fallback."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ..config import BACKEND_DIR, settings

_CANDIDATES = [
    settings.FONT_PATH,
    str(BACKEND_DIR / "assets" / "fonts" / "PatrickHand-Regular.ttf"),
    r"C:\Windows\Fonts\segoepr.ttf",      # Segoe Print
    r"C:\Windows\Fonts\segoesc.ttf",      # Segoe Script
    r"C:\Windows\Fonts\comic.ttf",
    "/usr/share/fonts/truetype/patrick-hand/PatrickHand-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Chalkboard.ttc",
    "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def font_path() -> str | None:
    for c in _CANDIDATES:
        if c and os.path.exists(c):
            return c
    return None


_DEVANAGARI = str(BACKEND_DIR / "assets" / "fonts" / "NotoSansDevanagari-Regular.ttf")


def script_of(text: str) -> str:
    """'devanagari' if the text contains Devanagari characters, else 'latin'."""
    return "devanagari" if any("\u0900" <= ch <= "\u097f" for ch in text or "") else "latin"


@lru_cache(maxsize=64)
def _devanagari_font(size: int):
    if os.path.exists(_DEVANAGARI):
        try:
            return ImageFont.truetype(_DEVANAGARI, size=size)
        except Exception:
            pass
    return get_font(size)


def font_for(size: int, text: str = ""):
    """Font for a piece of text: the handwriting font for Latin, Noto for Devanagari."""
    return _devanagari_font(size) if script_of(text) == "devanagari" else get_font(size)


@lru_cache(maxsize=64)
def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    p = font_path()
    if p:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

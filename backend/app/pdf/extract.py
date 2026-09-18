"""PDF analysis: per-page text extraction, structure hints, page thumbnails,
optional OCR for scanned pages, and the suitability check."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pypdfium2 as pdfium

from ..config import settings
from ..models import Suitability


@dataclass
class PageText:
    page: int                     # 1-indexed
    text: str
    chars: int
    headings: list[str] = field(default_factory=list)
    ocr: bool = False


@dataclass
class ExtractedDoc:
    page_count: int
    pages: list[PageText]
    title_guess: str = ""

    def as_chunks(self) -> list[dict]:
        return [
            {"page": p.page, "text": p.text, "headings": p.headings, "ocr": p.ocr}
            for p in self.pages
        ]

    def text_for_pages(self, pages: list[int], pad: int = 1, max_chars: int = 0) -> str:
        wanted = set()
        for p in pages:
            for q in range(p - pad, p + pad + 1):
                if 1 <= q <= self.page_count:
                    wanted.add(q)
        parts = [
            f"[Page {p.page}]\n{p.text.strip()}" for p in self.pages if p.page in wanted and p.text.strip()
        ]
        out = "\n\n".join(parts)
        if max_chars and len(out) > max_chars:
            out = out[:max_chars] + "\n...[truncated]"
        return out

    def full_text(self, max_chars: int) -> str:
        parts = [f"[Page {p.page}]\n{p.text.strip()}" for p in self.pages if p.text.strip()]
        total = sum(len(x) for x in parts)
        if total <= max_chars:
            return "\n\n".join(parts)
        # shrink every page proportionally so all pages still appear
        ratio = max_chars / max(total, 1)
        return "\n\n".join(x[: max(200, int(len(x) * ratio))] for x in parts)


_OFFICE_PREFIX_RE = re.compile(r"^\s*(?:microsoft\s+)?(?:office\s+)?(?:word|powerpoint|excel)\s*[-:–]\s*", re.I)
_FILE_EXT_RE = re.compile(r"\.(?:pptx?|docx?|xlsx?|pdf|odp|odt|key|pages|tex|txt)\s*$", re.I)
_GENERIC_TITLE_RE = re.compile(
    r"^(?:untitled(?:\s+\w+)?|presentation\s*\d*|(?:microsoft\s+)?(?:powerpoint|word|excel)(?:\s+(?:presentation|document|slides?))?"
    r"|document\s*\d*|slide\s*(?:show)?\s*\d*|book\s*\d*|new\s+(?:document|presentation)|title|pdf|scan\w*\s*\d*"
    r"|image\s*\d*|doc\s*\d*|file\s*\d*|\d+)$",
    re.I,
)


def clean_title(raw: str | None) -> str:
    """Strip Office export noise ("Microsoft PowerPoint - x.pptx") and reject
    placeholder titles such as "Presentation1" or "PowerPoint Presentation"."""
    t = re.sub(r"\s+", " ", str(raw or "")).strip()
    for _ in range(2):
        t = _FILE_EXT_RE.sub("", _OFFICE_PREFIX_RE.sub("", t)).strip(" -_:–")
    if len(t) < 3 or _GENERIC_TITLE_RE.match(t):
        return ""
    return t


_MINOR_WORDS = {"and", "or", "of", "the", "versus", "vs", "in", "to", "a", "an", "for", "with", "on"}
_HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Za-z0-9 ,:/&()\-]{2,70}$")


def _guess_headings(text: str) -> list[str]:
    heads = []
    for line in text.splitlines():
        s = line.strip()
        if 3 < len(s) < 72 and not s.endswith(".") and _HEADING_RE.match(s):
            core = re.sub(r"^\d+(?:\.\d+)*\s+", "", s)
            words = [w for w in core.split() if w.lower() not in _MINOR_WORDS]
            caps = sum(1 for w in words if w[:1].isupper())
            if words and caps >= len(words) * 0.6:
                heads.append(s)
    return heads[:8]


def _ocr_page(page: pdfium.PdfPage) -> Optional[str]:
    """Best-effort OCR through pytesseract if it is installed (optional dependency)."""
    try:
        import pytesseract  # type: ignore
    except Exception:
        return None
    try:
        img = page.render(scale=2).to_pil()
        return pytesseract.image_to_string(img)
    except Exception:
        return None


def extract(pdf_path: Path, max_pages: int = 0) -> ExtractedDoc:
    doc = pdfium.PdfDocument(str(pdf_path))
    n = len(doc)
    limit = min(n, max_pages) if max_pages else n
    pages: list[PageText] = []
    for i in range(limit):
        page = doc[i]
        text = ""
        try:
            tp = page.get_textpage()
            text = tp.get_text_range() or ""
            tp.close()
        except Exception:
            text = ""
        text = re.sub(r"[ \t]+\n", "\n", text).replace("\r", "")
        ocr = False
        if len(text.strip()) < 40:
            alt = _ocr_page(page)
            if alt and len(alt.strip()) > len(text.strip()):
                text, ocr = alt, True
        pages.append(PageText(page=i + 1, text=text, chars=len(text.strip()), headings=_guess_headings(text), ocr=ocr))
        page.close()
    title = ""
    try:
        meta = doc.get_metadata_dict()
        title = clean_title(meta.get("Title"))
    except Exception:
        pass
    if not title:
        title = next((h for p in pages for h in (clean_title(x) for x in p.headings) if h), "")
    doc.close()
    return ExtractedDoc(page_count=n, pages=pages, title_guess=title)


def page_count(pdf_path: Path) -> int:
    doc = pdfium.PdfDocument(str(pdf_path))
    n = len(doc)
    doc.close()
    return n


def render_page_png(pdf_path: Path, page_no: int, scale: float = 1.5) -> bytes:
    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[page_no - 1]
    img = page.render(scale=scale).to_pil().convert("RGB")
    page.close()
    doc.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_page_jpeg(pdf_path: Path, page_no: int, scale: float = 1.0, quality: int = 80) -> bytes:
    """Compact page image for the brain (~60-120 KB at scale 1.0)."""
    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[page_no - 1]
    img = page.render(scale=scale).to_pil().convert("RGB")
    page.close()
    doc.close()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def suitability(ex: ExtractedDoc) -> Suitability:
    warnings: list[str] = []
    recs: list[str] = []
    n = ex.page_count
    if n > settings.MAX_PAGES:
        warnings.append(f"PDF has {n} pages; the MVP limit is {settings.MAX_PAGES}. Only the first {settings.MAX_PAGES} were analysed.")
    empty = [p.page for p in ex.pages if p.chars < 40]
    ocr_pages = [p.page for p in ex.pages if p.ocr]
    garbage = 0
    for p in ex.pages:
        if p.chars >= 40:
            letters = sum(c.isalpha() or c.isspace() for c in p.text)
            if letters / max(len(p.text), 1) < 0.6:
                garbage += 1
    frac_empty = len(empty) / max(len(ex.pages), 1)
    if ocr_pages:
        warnings.append(f"{len(ocr_pages)} page(s) had no text layer and were OCR'd; accuracy may be reduced.")
    if empty and not ocr_pages:
        shown = ", ".join(map(str, empty[:8])) + ("..." if len(empty) > 8 else "")
        warnings.append(f"{len(empty)} page(s) contain little or no extractable text (pages {shown}). They may be scanned images or diagrams.")
        if frac_empty > 0.3:
            recs.append("Install Tesseract + pytesseract to enable OCR for scanned pages, or upload a text-based PDF.")
    if garbage:
        warnings.append(f"{garbage} page(s) look like low-quality or garbled text extraction.")
    total_chars = sum(p.chars for p in ex.pages)
    if total_chars < 1500:
        warnings.append("Very little text could be extracted from this PDF.")

    if frac_empty > 0.5 or total_chars < 1500:
        quality = "poor"
    elif frac_empty > 0.2 or garbage > max(1, len(ex.pages) * 0.15) or ocr_pages:
        quality = "fair"
    else:
        quality = "good"
    return Suitability(
        suitable=quality != "poor",
        page_count=n,
        quality=quality,
        warnings=warnings,
        recommendations=recs,
        ocr_pages=ocr_pages,
    )

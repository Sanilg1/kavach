"""Brain AI orchestration: topic map, teaching plans, follow-up answers.
Validates / normalises model output into the typed contract in app/models.py."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from ..config import settings
from ..models import Element, PlanPart, QuickCheck, Scene, TeachingPlan, Topic, TopicMap, Uncertainty
from ..pdf import extract as pdfx
from ..pdf.extract import ExtractedDoc, clean_title
from . import prompts
from .client import Attachment, complete_json_with_retry

log = logging.getLogger("kavach.planner")


def _pdf_attachment(pdf_path: Optional[Path]) -> list[Attachment]:
    if not (settings.BRAIN_VISION and pdf_path and pdf_path.exists()):
        return []
    size = pdf_path.stat().st_size
    if size > settings.BRAIN_MAX_PDF_MB * 1024 * 1024:
        log.info("PDF is %.1f MB; sending text only to the brain", size / 1e6)
        return []
    return [{"kind": "pdf", "bytes": pdf_path.read_bytes(), "name": "study-material"}]


def _page_attachments(pdf_path: Optional[Path], pages: list[int], page_count: int) -> list[Attachment]:
    if not (settings.BRAIN_VISION and pdf_path and pdf_path.exists()):
        return []
    out: list[Attachment] = []
    for p in sorted(set(pages))[: settings.BRAIN_MAX_ATTACH_PAGES]:
        if 1 <= p <= page_count:
            try:
                out.append({"kind": "image", "bytes": pdfx.render_page_jpeg(pdf_path, p), "format": "jpeg", "page": p})
            except Exception as ex:  # noqa: BLE001
                log.warning("could not render page %s for the brain: %s", p, ex)
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "topic"


def _pages(v, page_count: int) -> list[int]:
    out = []
    for p in v or []:
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if 1 <= p <= page_count and p not in out:
            out.append(p)
    return sorted(out)


# ------------------------------------------------------------------ topic map
def build_topic_map(ex: ExtractedDoc, pdf_path: Optional[Path] = None) -> TopicMap:
    attachments = _pdf_attachment(pdf_path)
    raw = complete_json_with_retry(
        prompts.TOPIC_MAP_SYSTEM,
        prompts.topic_map_user(ex.full_text(settings.MAX_DOC_CHARS), ex.title_guess, ex.page_count,
                               pdf_attached=bool(attachments)),
        attachments=attachments,
    )
    topics: list[Topic] = []
    seen: set[str] = set()
    for i, t in enumerate(raw.get("topics") or []):
        name = str(t.get("name") or f"Topic {i + 1}").strip()
        tid = _slug(str(t.get("topic_id") or name))
        while tid in seen:
            tid += "-2"
        seen.add(tid)
        shorts = max(1, min(4, int(t.get("estimated_shorts") or 1)))
        topics.append(Topic(
            topic_id=tid,
            name=name[:80],
            description=str(t.get("description") or "")[:300],
            source_pages=_pages(t.get("source_pages"), ex.page_count),
            prerequisites=[_slug(str(p)) for p in (t.get("prerequisites") or [])],
            difficulty=t.get("difficulty") if t.get("difficulty") in ("easy", "medium", "hard") else "medium",
            estimated_shorts=shorts,
            estimated_duration=max(30, min(240, int(t.get("estimated_duration") or shorts * 45))),
            learning_order=int(t.get("learning_order") or (i + 1)),
            recommended=bool(t.get("recommended", True)),
            selected=bool(t.get("recommended", True)),
        ))
    ids = {t.topic_id for t in topics}
    for t in topics:
        t.prerequisites = [p for p in t.prerequisites if p in ids and p != t.topic_id]
    topics.sort(key=lambda t: t.learning_order)
    for i, t in enumerate(topics):
        t.learning_order = i + 1
    return TopicMap(
        title=(clean_title(raw.get("title")) or ex.title_guess or "Your document")[:120],
        summary=str(raw.get("summary") or ""),
        topics=topics,
        uncertainties=[
            Uncertainty(reason=str(u.get("reason", "")), source_pages=_pages(u.get("source_pages"), ex.page_count))
            for u in (raw.get("uncertainties") or []) if u.get("reason")
        ],
    )


# ------------------------------------------------------------------ teaching plan
_VALID_TYPES = {"TEXT", "BOX", "CIRCLE", "LINE", "ARROW", "DIAGRAM", "EQUATION", "TABLE", "HIGHLIGHT", "IMAGE", "FLOW", "TIMELINE"}
_VALID_ANIMS = {"DRAW", "WRITE", "FADE_IN", "FADE_OUT", "MOVE", "HIGHLIGHT", "ARROW_FLOW", "SEQUENTIAL_REVEAL"}
_DEFAULT_ANIM = {"TEXT": "WRITE", "EQUATION": "WRITE", "HIGHLIGHT": "HIGHLIGHT", "ARROW": "DRAW", "LINE": "DRAW",
                 "FLOW": "SEQUENTIAL_REVEAL", "TIMELINE": "SEQUENTIAL_REVEAL", "TABLE": "SEQUENTIAL_REVEAL"}


def _norm_element(raw: dict, idx: int, used: set[str]) -> Optional[Element]:
    if not isinstance(raw, dict):
        return None
    t = str(raw.get("type", "TEXT")).upper()
    if t not in _VALID_TYPES:
        t = "TEXT"
    anim = str(raw.get("animation") or _DEFAULT_ANIM.get(t, "FADE_IN")).upper()
    if anim not in _VALID_ANIMS:
        anim = _DEFAULT_ANIM.get(t, "FADE_IN")
    eid = str(raw.get("id") or f"{t.lower()}{idx}")
    while eid in used:
        eid = f"{eid}_{idx}"
    used.add(eid)
    data = dict(raw)
    data.update(id=eid, type=t, animation=anim)
    if "from" not in data and "from_id" in data:
        data["from"] = data.pop("from_id")
    if "to" not in data and "to_id" in data:
        data["to"] = data.pop("to_id")
    for k in ("from", "to", "target", "text", "label", "color"):
        if data.get(k) is None:
            data[k] = ""
        elif not isinstance(data[k], str):
            data[k] = str(data[k])
    if data.get("size") not in ("title", "large", "normal", "small"):
        data["size"] = "normal"
    if data.get("direction") not in ("horizontal", "vertical"):
        data["direction"] = "horizontal"
    for k in ("x", "y", "w", "h", "x2", "y2", "to_x", "to_y"):
        v = data.get(k)
        try:
            data[k] = None if v is None else max(0.0, min(100.0, float(v)))
        except (TypeError, ValueError):
            data[k] = None
    data["steps"] = [str(s) for s in (data.get("steps") or []) if str(s).strip()][:6]
    data["rows"] = [[str(c) for c in r][:4] for r in (data.get("rows") or []) if isinstance(r, list)][:6]
    data["nodes"] = [n for n in (data.get("nodes") or []) if isinstance(n, dict) and n.get("id")][:8]
    data["edges"] = [e for e in (data.get("edges") or []) if isinstance(e, dict)][:12]
    try:
        data["page"] = int(data["page"]) if data.get("page") is not None else None
    except (TypeError, ValueError):
        data["page"] = None
    if t == "TEXT" and not data["text"]:
        data["text"] = data["label"]
    if t in ("BOX", "CIRCLE") and not data["label"]:
        data["label"] = data["text"]
    return Element.model_validate(data)


def _norm_scene(raw: dict, idx: int) -> Optional[Scene]:
    if not isinstance(raw, dict):
        return None
    narration = str(raw.get("narration") or "").strip()
    if not narration:
        return None
    used: set[str] = set()
    elements = [e for e in (_norm_element(x, i, used) for i, x in enumerate(raw.get("elements") or [])) if e]
    return Scene(scene_id=f"s{idx + 1}", narration=narration, clear=bool(raw.get("clear", False)), elements=elements)


def _norm_quick_check(raw) -> Optional[QuickCheck]:
    if not isinstance(raw, dict) or not raw.get("question"):
        return None
    options = [str(o) for o in (raw.get("options") or [])][:4]
    if len(options) < 2:
        return None
    try:
        ans = int(raw.get("answer", 0))
    except (TypeError, ValueError):
        ans = 0
    return QuickCheck(question=str(raw["question"]), options=options, answer=max(0, min(len(options) - 1, ans)),
                      explanation=str(raw.get("explanation") or ""))


def normalise_plan(raw: dict, topic: dict, page_count: int) -> TeachingPlan:
    parts: list[PlanPart] = []
    for i, p in enumerate(raw.get("parts") or []):
        if not isinstance(p, dict):
            continue
        scenes = [s for s in (_norm_scene(x, j) for j, x in enumerate(p.get("scenes") or [])) if s]
        if not scenes and p.get("script"):
            scenes = [Scene(scene_id="s1", narration=str(p["script"]), elements=[
                Element(id="title", type="TEXT", text=str(p.get("title") or topic["name"]), size="title", x=50, y=8, animation="WRITE")
            ])]
        if not scenes:
            continue
        unc = p.get("uncertainty")
        parts.append(PlanPart(
            part=i + 1,
            title=str(p.get("title") or f"{topic['name']} - part {i + 1}")[:120],
            duration_target=max(20, min(90, int(p.get("duration_target") or 45))),
            script=" ".join(s.narration for s in scenes),
            visual_plan=[str(v) for v in (p.get("visual_plan") or [])][:12],
            scenes=scenes,
            sources=_pages(p.get("sources"), page_count) or _pages(topic.get("source_pages"), page_count),
            ai_added_context=[str(a) for a in (p.get("ai_added_context") or []) if str(a).strip()],
            uncertainty=Uncertainty(reason=str(unc.get("reason", "")), source_pages=_pages(unc.get("source_pages"), page_count))
            if isinstance(unc, dict) and unc.get("reason") else None,
            quick_check=_norm_quick_check(p.get("quick_check")),
        ))
    if not parts:
        raise ValueError("Brain returned a teaching plan with no usable parts")
    top_qc = _norm_quick_check(raw.get("quick_check"))
    for p in parts:
        if p.quick_check is None:
            p.quick_check = top_qc
    return TeachingPlan(
        topic=str(raw.get("topic") or topic["name"]),
        topic_id=topic["topic_id"],
        learning_order=int(topic.get("learning_order") or 0),
        parts=parts,
        quick_check=top_qc,
    )


def build_teaching_plan(
    topic: dict,
    topic_map: dict,
    ex: ExtractedDoc,
    ai_enhanced: bool = False,
    feedback: Optional[str] = None,
    previous_plan: Optional[dict] = None,
    pdf_path: Optional[Path] = None,
    language: str = "en",
) -> TeachingPlan:
    pages = topic.get("source_pages") or [1]
    context = ex.text_for_pages(pages, pad=1, max_chars=60000)
    if len(context) < 400:  # tiny excerpt: give the brain more of the document
        context = ex.full_text(40000)
    attachments = _page_attachments(pdf_path, pages, ex.page_count)
    raw = complete_json_with_retry(
        prompts.PLAN_SYSTEM,
        prompts.plan_user(topic, topic_map, context, ai_enhanced, feedback, previous_plan,
                          image_pages=[a["page"] for a in attachments], language=language),
        attachments=attachments,
    )
    return normalise_plan(raw, topic, ex.page_count)


# ------------------------------------------------------------------ follow-up Q&A
def answer_question(question: str, part: dict, ex: ExtractedDoc, ai_enhanced: bool, language: str = "en") -> dict:
    sources = part.get("sources") or [1]
    context = ex.text_for_pages(sources, pad=2, max_chars=50000)
    raw = complete_json_with_retry(
        prompts.ASK_SYSTEM,
        prompts.ask_user(question, part.get("script", ""), context, ai_enhanced, sources, language=language),
        max_tokens=4000,
    )
    return {
        "answer": str(raw.get("answer") or "").strip(),
        "sources": _pages(raw.get("sources"), ex.page_count),
        "ai_added_context": [str(a) for a in (raw.get("ai_added_context") or []) if str(a).strip()],
        "uncertainty": raw.get("uncertainty") if isinstance(raw.get("uncertainty"), (str, dict)) else None,
    }

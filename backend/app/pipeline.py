"""End-to-end pipeline steps. Each function is self-contained so it can run in a
background thread today or inside a Lambda tomorrow."""
from __future__ import annotations

import io
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from PIL import Image

from . import storage as st
from .brain import planner
from .config import settings
from .db import db, now_iso
from .models import PlanPart, TeachingPlan, Topic, TopicMap
from .pdf import extract as pdfx
from .render.renderer import Renderer
from .tts.synth import tts
from .video.ffmpeg import concat_audio, encode_video

log = logging.getLogger("kavach.pipeline")


def _fmt_pages(pages: list[int]) -> str:
    if not pages:
        return ""
    pages = sorted(set(pages))
    runs, start, prev = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        runs.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = p
    runs.append(f"{start}-{prev}" if start != prev else str(start))
    return ("page " if len(pages) == 1 else "pages ") + ", ".join(runs)


def _load_doc(doc_id: str) -> pdfx.ExtractedDoc:
    chunks = st.get_json(st.k_chunks(doc_id))
    pages = [pdfx.PageText(page=c["page"], text=c["text"], chars=len(c["text"].strip()),
                           headings=c.get("headings", []), ocr=c.get("ocr", False)) for c in chunks["pages"]]
    return pdfx.ExtractedDoc(page_count=chunks["page_count"], pages=pages, title_guess=chunks.get("title", ""))


# ---------------------------------------------------------------- 1. analyze
def analyze_document(doc_id: str) -> None:
    doc = db.get_document(doc_id)
    if not doc:
        return
    try:
        db.update_document(doc_id, status="PROCESSING", progress="Reading document")
        pdf_path = st.storage.local_path(st.k_upload(doc_id))
        ex = pdfx.extract(pdf_path, max_pages=settings.MAX_PAGES)
        ex.title_guess = ex.title_guess or pdfx.clean_title(Path(doc.get("filename") or "").stem)
        suit = pdfx.suitability(ex)
        st.put_json(st.k_chunks(doc_id), {"page_count": ex.page_count, "title": ex.title_guess, "pages": ex.as_chunks()})
        db.update_document(doc_id, status="ANALYZING", progress="Identifying concepts", page_count=ex.page_count,
                           quality=suit.quality, suitability=suit.model_dump(), title=ex.title_guess)

        tmap: TopicMap = planner.build_topic_map(ex)
        st.put_json(st.k_topic_map(doc_id), tmap.model_dump())
        db.put_topics(doc_id, [t.model_dump() for t in tmap.topics])
        est_shorts = sum(t.estimated_shorts for t in tmap.topics if t.selected)
        est_dur = sum(t.estimated_duration for t in tmap.topics if t.selected)
        db.update_document(doc_id, status="READY", progress="Learning path ready", title=tmap.title,
                           summary=tmap.summary, concept_count=len(tmap.topics), estimated_shorts=est_shorts,
                           estimated_duration=est_dur, uncertainties=[u.model_dump() for u in tmap.uncertainties])
    except Exception as e:  # noqa: BLE001
        log.exception("analysis failed for %s", doc_id)
        db.update_document(doc_id, status="FAILED", error=str(e)[:800], progress="Failed")


# ---------------------------------------------------------------- 2. topic selection
def apply_topic_update(doc_id: str, selected_ids: list[str], order: list[str], added: list[dict]) -> list[dict]:
    topics = db.list_topics(doc_id)
    by_id = {t["topic_id"]: t for t in topics}
    for a in added:
        name = str(a.get("name", "")).strip()
        if not name:
            continue
        tid = planner._slug(name)
        while tid in by_id:
            tid += "-2"
        pages = planner._pages(a.get("source_pages"), 10_000)
        t = Topic(topic_id=tid, name=name[:80], description=str(a.get("description", ""))[:300], source_pages=pages,
                  estimated_shorts=1, estimated_duration=45, learning_order=len(topics) + 1, recommended=False,
                  selected=True, user_added=True).model_dump()
        topics.append(t)
        by_id[tid] = t
        selected_ids = list(selected_ids) + [tid]
    sel = set(selected_ids)
    for t in topics:
        t["selected"] = t["topic_id"] in sel
    if order:
        rank = {tid: i for i, tid in enumerate(order)}
        topics.sort(key=lambda t: (rank.get(t["topic_id"], 10_000), t.get("learning_order", 0)))
    for i, t in enumerate(topics):
        t["learning_order"] = i + 1
    db.put_topics(doc_id, topics)
    est_shorts = sum(t["estimated_shorts"] for t in topics if t["selected"])
    est_dur = sum(t["estimated_duration"] for t in topics if t["selected"])
    db.update_document(doc_id, estimated_shorts=est_shorts, estimated_duration=est_dur)
    # keep topic_map.json in sync so the brain sees the student's path
    try:
        tm = st.get_json(st.k_topic_map(doc_id))
        tm["topics"] = topics
        st.put_json(st.k_topic_map(doc_id), tm)
    except Exception:  # noqa: BLE001
        pass
    return topics


# ---------------------------------------------------------------- 3. generate
def generate_document(doc_id: str, ai_enhanced: bool, topic_ids: Optional[list[str]] = None) -> None:
    doc = db.get_document(doc_id)
    if not doc:
        return
    try:
        db.update_document(doc_id, status="GENERATING", progress="Planning lessons")
        ex = _load_doc(doc_id)
        tmap = st.get_json(st.k_topic_map(doc_id))
        topics = [t for t in db.list_topics(doc_id) if t.get("selected")]
        if topic_ids:
            topics = [t for t in topics if t["topic_id"] in set(topic_ids)]
        if not topics:
            raise ValueError("No topics selected")

        # create PENDING reels first so the feed can show progress
        reel_ids: dict[tuple[str, int], str] = {}
        existing = {(r["topic_id"], r["part"]): r for r in db.list_reels(doc_id)}
        for t in topics:
            for part in range(1, int(t.get("estimated_shorts", 1)) + 1):
                key = (t["topic_id"], part)
                rid = existing[key]["reel_id"] if key in existing else uuid.uuid4().hex[:12]
                reel_ids[key] = rid
                db.put_reel({
                    "reel_id": rid, "document_id": doc_id, "topic_id": t["topic_id"], "topic_name": t["name"],
                    "learning_order": t.get("learning_order", 0), "part": part, "title": f"{t['name']} - part {part}",
                    "status": "PENDING", "created_at": now_iso(), "ai_enhanced": ai_enhanced,
                })

        failures = 0
        for t in topics:
            db.update_document(doc_id, progress=f"Planning: {t['name']}")
            for part in range(1, int(t.get("estimated_shorts", 1)) + 1):
                db.update_reel(reel_ids[(t["topic_id"], part)], status="PLANNING")
            try:
                plan = planner.build_teaching_plan(t, tmap, ex, ai_enhanced=ai_enhanced)
            except Exception as e:  # noqa: BLE001
                log.exception("plan failed for topic %s", t["topic_id"])
                failures += 1
                for part in range(1, int(t.get("estimated_shorts", 1)) + 1):
                    db.update_reel(reel_ids[(t["topic_id"], part)], status="FAILED", error=str(e)[:500])
                continue
            st.put_json(st.k_plan(doc_id, t["topic_id"]), plan.model_dump())
            # reconcile reel rows with the number of parts the brain actually chose
            planned = {p.part for p in plan.parts}
            for part in range(1, int(t.get("estimated_shorts", 1)) + 1):
                if part not in planned:
                    db.update_reel(reel_ids[(t["topic_id"], part)], status="FAILED", error="Brain merged this part into another short", hidden=True)
            for p in plan.parts:
                key = (t["topic_id"], p.part)
                rid = reel_ids.get(key) or uuid.uuid4().hex[:12]
                if key not in reel_ids:
                    db.put_reel({"reel_id": rid, "document_id": doc_id, "topic_id": t["topic_id"], "topic_name": t["name"],
                                 "learning_order": t.get("learning_order", 0), "part": p.part, "status": "PENDING",
                                 "created_at": now_iso(), "ai_enhanced": ai_enhanced})
                    reel_ids[key] = rid
                try:
                    render_reel(doc_id, rid, plan, p)
                except Exception as e:  # noqa: BLE001
                    log.exception("render failed for reel %s", rid)
                    failures += 1
                    db.update_reel(rid, status="FAILED", error=str(e)[:500])
        done = [r for r in db.list_reels(doc_id) if r.get("status") == "COMPLETED"]
        status = "COMPLETED" if done else "FAILED"
        db.update_document(doc_id, status=status, progress="Done" if done else "Generation failed",
                           reels_completed=len(done), reels_failed=failures)
    except Exception as e:  # noqa: BLE001
        log.exception("generation failed for %s", doc_id)
        db.update_document(doc_id, status="FAILED", error=str(e)[:800], progress="Failed")


# ---------------------------------------------------------------- 4. narrate + render one short
def render_reel(doc_id: str, reel_id: str, plan: TeachingPlan, part: PlanPart) -> dict:
    work = settings.WORK_DIR / doc_id / reel_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    db.update_reel(reel_id, status="NARRATING", title=part.title, part=part.part, sources=part.sources,
                   ai_added_context=part.ai_added_context, uncertainty=part.uncertainty.model_dump() if part.uncertainty else None,
                   quick_check=(part.quick_check or plan.quick_check).model_dump() if (part.quick_check or plan.quick_check) else None,
                   script=part.script, visual_plan=part.visual_plan)
    t_start = time.time()
    segments: list[tuple[Path, float]] = []
    scene_durations: list[float] = []
    for i, scene in enumerate(part.scenes):
        mp3 = work / f"scene_{i + 1}.mp3"
        audio_len = tts.synthesize(scene.narration, mp3)
        pad = max(0.7, 3.0 - audio_len)         # breathing room after each scene
        segments.append((mp3, pad))
        scene_durations.append(audio_len + pad)
    audio_path = work / "narration.m4a"
    concat_audio(segments, audio_path)

    db.update_reel(reel_id, status="RENDERING")
    pdf_path = st.storage.local_path(st.k_upload(doc_id))

    def page_image(page: int):
        return Image.open(io.BytesIO(pdfx.render_page_png(pdf_path, page, scale=1.2)))

    r = Renderer(settings.VIDEO_WIDTH, settings.VIDEO_HEIGHT, settings.VIDEO_FPS, page_image=page_image,
                 footer=f"Source: PDF {_fmt_pages(part.sources)}" if part.sources else "")
    r.build(part, scene_durations)
    mp4 = work / "short.mp4"
    encode_video(r.frames(), settings.VIDEO_WIDTH, settings.VIDEO_HEIGHT, settings.VIDEO_FPS, audio_path, mp4)
    poster = work / "poster.jpg"
    r.poster().save(poster, quality=85)

    video_key = st.k_video(doc_id, reel_id)
    st.storage.put_file(video_key, mp4, "video/mp4")
    st.storage.put_file(st.k_thumb(doc_id, reel_id), poster, "image/jpeg")
    st.storage.put_file(st.k_audio(doc_id, reel_id), audio_path, "audio/mp4")
    duration = round(sum(scene_durations), 1)
    reel = db.update_reel(reel_id, status="COMPLETED", video_s3_key=video_key, thumb_s3_key=st.k_thumb(doc_id, reel_id),
                          duration=duration, render_seconds=round(time.time() - t_start, 1), error=None)
    shutil.rmtree(work, ignore_errors=True)
    return reel


# ---------------------------------------------------------------- 5. regenerate with feedback
def regenerate_reel(reel_id: str, feedback: str, ai_enhanced: bool) -> None:
    reel = db.get_reel(reel_id)
    if not reel:
        return
    doc_id, topic_id = reel["document_id"], reel["topic_id"]
    try:
        db.update_reel(reel_id, status="PLANNING", feedback=feedback)
        ex = _load_doc(doc_id)
        tmap = st.get_json(st.k_topic_map(doc_id))
        topic = next((t for t in db.list_topics(doc_id) if t["topic_id"] == topic_id), None)
        if not topic:
            raise ValueError("Topic not found")
        previous = st.get_json(st.k_plan(doc_id, topic_id)) if st.storage.exists(st.k_plan(doc_id, topic_id)) else None
        # regenerate just this part: ask the brain for the whole topic, pick the matching part
        topic = dict(topic, estimated_shorts=1)
        prev_part = None
        if previous:
            prev_part = next((p for p in previous.get("parts", []) if p.get("part") == reel.get("part")), None)
            previous = {"parts": [prev_part]} if prev_part else previous
        topic["description"] = f"{topic.get('description', '')} Focus only on: {reel.get('title', '')}".strip()
        plan = planner.build_teaching_plan(topic, tmap, ex, ai_enhanced=ai_enhanced, feedback=feedback, previous_plan=previous)
        want = int(reel.get("part", 1))
        part = next((p for p in plan.parts if p.part == want), plan.parts[0])
        part.part = want
        if previous and prev_part:  # persist the new version of this part in the plan file
            full = st.get_json(st.k_plan(doc_id, topic_id))
            full["parts"] = [part.model_dump() if p.get("part") == part.part else p for p in full["parts"]]
            st.put_json(st.k_plan(doc_id, topic_id), full)
        render_reel(doc_id, reel_id, plan, part)
        db.update_reel(reel_id, regenerated=int(reel.get("regenerated", 0)) + 1)
    except Exception as e:  # noqa: BLE001
        log.exception("regeneration failed for %s", reel_id)
        db.update_reel(reel_id, status="FAILED", error=str(e)[:500])


# ---------------------------------------------------------------- 6. follow-up Q&A
def ask(reel_id: str, question: str, ai_enhanced: bool) -> dict:
    reel = db.get_reel(reel_id)
    if not reel:
        raise ValueError("Reel not found")
    ex = _load_doc(reel["document_id"])
    return planner.answer_question(question, reel, ex, ai_enhanced)

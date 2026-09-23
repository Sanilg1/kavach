"""Kavach backend API (FastAPI).

POST /documents                      upload PDF/DOCX/PPTX/TXT/MD -> converted to PDF, analysis starts
GET  /documents/{id}                 status + suitability + estimates
GET  /documents/{id}/topics          topic map / learning path
POST /documents/{id}/topics          add/remove/reorder topics
POST /documents/{id}/generate        generate revision shorts
GET  /documents/{id}/reels           reel feed
GET  /reels/{reel_id}                one reel
POST /reels/{reel_id}/regenerate     feedback-driven regeneration
POST /reels/{reel_id}/ask            follow-up question
POST /reels/{reel_id}/feedback       record feedback (no regeneration)
POST /documents/{id}/combine         build one combined lesson MP4 from all shorts
GET  /documents/{id}/notes.md        downloadable revision notes (scripts + quick checks)
GET  /media/{key}                    local-storage media (S3 mode returns presigned URLs instead)
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import pipeline
from . import storage as st
from .brain.client import brain_status
from .config import settings
from .db import db, now_iso
from .models import AskRequest, FeedbackRequest, GenerateRequest, RegenerateRequest, TopicUpdateRequest
from .pdf import convert
from .pdf.extract import page_count
from .worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kavach.api")

@contextlib.asynccontextmanager
async def _lifespan(_app):
    _recover_interrupted_jobs()
    yield


app = FastAPI(title="Kavach API", version="0.2.0", description="Compress the delivery, not the knowledge.", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.CORS_ORIGINS or ["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


def _recover_interrupted_jobs() -> None:
    """A crash or redeploy kills in-flight background jobs; mark them so the UI does not
    show a spinner forever and the student can regenerate."""
    try:
        for d in db.list_documents():
            if d.get("status") in ("PROCESSING", "ANALYZING"):
                db.update_document(d["document_id"], status="FAILED", progress="Interrupted by a server restart",
                                   error="Analysis was interrupted by a server restart. Please upload again.")
            elif d.get("status") == "GENERATING":
                db.update_document(d["document_id"], status="READY", progress="Interrupted by a server restart",
                                   error="Generation was interrupted by a server restart. Generate again to resume.")
                for r in db.list_reels(d["document_id"]):
                    if r.get("status") in ("PENDING", "PLANNING", "NARRATING", "RENDERING"):
                        db.update_reel(r["reel_id"], status="FAILED", error="Interrupted by a server restart")
            if d.get("combined_status") == "BUILDING":
                db.update_document(d["document_id"], combined_status="FAILED", combined_error="Interrupted by a server restart")
    except Exception:  # noqa: BLE001
        log.exception("startup recovery failed")


def _media_url(key: str | None, download_as: str | None = None) -> str | None:
    return st.storage.url(key, download_as) if key else None


def _safe_name(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in (s or "")).strip()[:60] or "kavach"


def _public_doc(doc: dict) -> dict:
    d = dict(doc)
    d.pop("error_trace", None)
    d["combined_url"] = _media_url(d.get("combined_video_key")) if d.get("combined_status") == "COMPLETED" else None
    d["combined_download_url"] = (_media_url(d.get("combined_video_key"), f"{_safe_name(d.get('title', ''))} - full lesson.mp4")
                                  if d.get("combined_status") == "COMPLETED" else None)
    return d


def _public_reel(r: dict) -> dict:
    r = dict(r)
    r["video_url"] = _media_url(r.get("video_s3_key")) if r.get("status") == "COMPLETED" else None
    r["thumb_url"] = _media_url(r.get("thumb_s3_key")) if r.get("status") == "COMPLETED" else None
    r["download_url"] = (_media_url(r.get("video_s3_key"), f"{_safe_name(r.get('title', ''))}.mp4")
                         if r.get("status") == "COMPLETED" else None)
    r["sources_label"] = pipeline._fmt_pages(r.get("sources") or [])
    return r


# ---------------------------------------------------------------- abuse guard
_EXPENSIVE = ("/generate", "/regenerate", "/ask", "/combine")
_hits: dict[str, list[float]] = {}
_hits_lock = __import__("threading").Lock()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """Per-IP sliding-window limit on the requests that cost LLM/TTS/render time."""
    path = request.url.path
    costly = request.method == "POST" and (path == "/documents" or path.endswith(_EXPENSIVE))
    if costly and settings.RATE_LIMIT_PER_HOUR > 0:
        import time as _t

        ip, now = _client_ip(request), _t.time()
        with _hits_lock:
            recent = [t for t in _hits.get(ip, []) if now - t < 3600]
            if len(recent) >= settings.RATE_LIMIT_PER_HOUR:
                retry = int(3600 - (now - recent[0])) + 1
                return JSONResponse({"detail": f"Too many requests - try again in {retry // 60 + 1} min."},
                                    status_code=429, headers={"Retry-After": str(retry)})
            recent.append(now)
            _hits[ip] = recent
    return await call_next(request)


@app.get("/languages")
def languages():
    from .tts.synth import LANGUAGES

    return [{"id": k, "label": v["label"], "voice": v["voice"]} for k, v in LANGUAGES.items()]


@app.get("/health")
def health():
    return {"ok": True, "mode": settings.MODE, "storage": settings.STORAGE, "db": settings.DB,
            "brain": settings.BRAIN, "tts": settings.TTS, "model": settings.BEDROCK_MODEL,
            "brain_status": brain_status()}


# ---------------------------------------------------------------- documents
@app.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    filename = file.filename or "upload.pdf"
    if not convert.is_supported(filename):
        raise HTTPException(400, f"Unsupported file type. Please upload {convert.ACCEPT_LABEL}.")
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
    kind = convert.source_type(filename)
    if kind == "pdf" and not data.startswith(b"%PDF"):
        raise HTTPException(400, "This file does not look like a PDF.")
    doc_id = uuid.uuid4().hex[:12]
    src = settings.WORK_DIR / f"{doc_id}.{kind}"
    src.write_bytes(data)
    try:
        pdf_path = convert.convert_to_pdf(src, filename, settings.WORK_DIR / f"{doc_id}.pdf")
        n = page_count(pdf_path)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        src.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not read this {kind.upper()} file: {str(e)[:200]}")
    if n > settings.MAX_PAGES:
        src.unlink(missing_ok=True)
        pdf_path.unlink(missing_ok=True)
        raise HTTPException(400, f"The document has {n} pages; the maximum is {settings.MAX_PAGES}.")
    st.storage.put_file(st.k_upload(doc_id), pdf_path, "application/pdf")
    if kind != "pdf":
        st.storage.put_file(f"uploads/{doc_id}.{kind}", src)
        src.unlink(missing_ok=True)
    pdf_path.unlink(missing_ok=True)
    doc = {
        "document_id": doc_id, "filename": filename, "source_type": kind, "page_count": n, "status": "UPLOADED",
        "progress": "Uploaded", "created_at": now_iso(), "size_bytes": len(data),
    }
    db.put_document(doc)
    worker.submit(pipeline.analyze_document, doc_id)
    return _public_doc(doc)


@app.get("/documents")
def list_documents():
    return [_public_doc(d) for d in db.list_documents()]


@app.get("/documents/{doc_id}")
def get_document(doc_id: str):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return _public_doc(doc)


@app.get("/documents/{doc_id}/topics")
def get_topics(doc_id: str):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    topics = db.list_topics(doc_id)
    return {
        "document_id": doc_id, "title": doc.get("title"), "summary": doc.get("summary"),
        "status": doc.get("status"), "topics": topics, "uncertainties": doc.get("uncertainties", []),
        "estimated_shorts": sum(t.get("estimated_shorts", 1) for t in topics if t.get("selected")),
        "estimated_duration": sum(t.get("estimated_duration", 45) for t in topics if t.get("selected")),
    }


@app.post("/documents/{doc_id}/topics")
def update_topics(doc_id: str, body: TopicUpdateRequest):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.get("status") in ("UPLOADED", "PROCESSING", "ANALYZING"):
        raise HTTPException(409, "Document is still being analysed")
    topics = pipeline.apply_topic_update(doc_id, body.selected_topic_ids, body.order, body.added_topics)
    return get_topics(doc_id) | {"topics": topics}


@app.post("/documents/{doc_id}/generate")
def generate(doc_id: str, body: GenerateRequest | None = None):
    body = body or GenerateRequest()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.get("status") in ("UPLOADED", "PROCESSING", "ANALYZING"):
        raise HTTPException(409, "Document is still being analysed")
    if doc.get("status") == "GENERATING":
        raise HTTPException(409, "Generation already in progress")
    if not any(t.get("selected") for t in db.list_topics(doc_id)):
        raise HTTPException(400, "Select at least one topic")
    if not db.transition_document(doc_id, "status", {"READY", "COMPLETED", "FAILED"}, status="GENERATING",
                                  progress="Queued", ai_enhanced=body.ai_enhanced, language=body.language, error=None):
        raise HTTPException(409, "Generation already in progress")
    worker.submit(pipeline.generate_document, doc_id, body.ai_enhanced, body.topic_ids, body.language)
    return {"document_id": doc_id, "status": "GENERATING"}


@app.get("/documents/{doc_id}/reels")
def list_reels(doc_id: str):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    reels = [_public_reel(r) for r in db.list_reels(doc_id) if not r.get("hidden")]
    return {"document_id": doc_id, "status": doc.get("status"), "progress": doc.get("progress"), "reels": reels}


@app.post("/documents/{doc_id}/combine")
def combine(doc_id: str):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.get("status") == "GENERATING":
        raise HTTPException(409, "Wait for generation to finish")
    if doc.get("combined_status") == "BUILDING":
        raise HTTPException(409, "Combined lesson is already being built")
    if not any(r.get("status") == "COMPLETED" for r in db.list_reels(doc_id)):
        raise HTTPException(400, "No completed shorts yet")
    if not db.transition_document(doc_id, "combined_status", {None, "COMPLETED", "FAILED"}, combined_status="BUILDING"):
        raise HTTPException(409, "Combined lesson is already being built")
    worker.submit(pipeline.combine_document, doc_id)
    return {"document_id": doc_id, "combined_status": "BUILDING"}


@app.get("/documents/{doc_id}/notes.md")
def notes(doc_id: str):
    from fastapi.responses import PlainTextResponse

    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    md = pipeline.notes_markdown(doc_id)
    name = (doc.get("title") or "kavach-notes").replace('"', "")[:60]
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


# ---------------------------------------------------------------- reels
@app.get("/reels/{reel_id}")
def get_reel(reel_id: str):
    r = db.get_reel(reel_id)
    if not r:
        raise HTTPException(404, "Reel not found")
    return _public_reel(r)


@app.post("/reels/{reel_id}/regenerate")
def regenerate(reel_id: str, body: RegenerateRequest):
    r = db.get_reel(reel_id)
    if not r:
        raise HTTPException(404, "Reel not found")
    doc = db.get_document(r["document_id"]) or {}
    if doc.get("status") == "GENERATING":
        raise HTTPException(409, "Wait for the other shorts to finish generating")
    if not db.transition_reel(reel_id, "status", {"COMPLETED", "FAILED", "PENDING"}, status="PLANNING", feedback=body.feedback):
        raise HTTPException(409, "Reel is already being generated")
    worker.submit(pipeline.regenerate_reel, reel_id, body.feedback, body.ai_enhanced)
    return {"reel_id": reel_id, "status": "PLANNING"}


@app.post("/reels/{reel_id}/ask")
def ask(reel_id: str, body: AskRequest):
    if not body.question.strip():
        raise HTTPException(400, "Question is empty")
    try:
        return pipeline.ask(reel_id, body.question.strip(), body.ai_enhanced)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("ask failed")
        raise HTTPException(502, f"Brain AI error: {e}")


@app.post("/reels/{reel_id}/feedback")
def feedback(reel_id: str, body: FeedbackRequest):
    r = db.get_reel(reel_id)
    if not r:
        raise HTTPException(404, "Reel not found")
    hist = list(r.get("feedback_history") or []) + [{"feedback": body.feedback, "note": body.note, "at": now_iso()}]
    db.update_reel(reel_id, feedback=body.feedback, feedback_history=hist[-20:])
    return {"ok": True}


@app.get("/reels/{reel_id}/plan")
def get_plan(reel_id: str):
    r = db.get_reel(reel_id)
    if not r:
        raise HTTPException(404, "Reel not found")
    key = st.k_plan(r["document_id"], r["topic_id"])
    if not st.storage.exists(key):
        raise HTTPException(404, "Plan not generated yet")
    plan = st.get_json(key)
    part = next((p for p in plan.get("parts", []) if p.get("part") == r.get("part")), None)
    return {"topic": plan.get("topic"), "part": part}


# ---------------------------------------------------------------- media (local storage mode)
@app.get("/media/{key:path}")
def media(key: str, download: str | None = None):
    if settings.STORAGE != "local":
        return JSONResponse({"url": st.storage.url(key, download)})
    p: Path = st.storage.local_path(key)
    if not p.exists() or ".." in key:
        raise HTTPException(404, "Not found")
    return FileResponse(p, filename=download) if download else FileResponse(p)


# AWS Lambda entry point (container image or zip with Mangum)
try:
    from mangum import Mangum

    handler = Mangum(app)
except Exception:  # noqa: BLE001
    handler = None

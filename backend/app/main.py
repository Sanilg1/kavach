"""Kavach backend API (FastAPI).

POST /documents                      upload PDF -> analysis starts in background
GET  /documents/{id}                 status + suitability + estimates
GET  /documents/{id}/topics          topic map / learning path
POST /documents/{id}/topics          add/remove/reorder topics
POST /documents/{id}/generate        generate revision shorts
GET  /documents/{id}/reels           reel feed
GET  /reels/{reel_id}                one reel
POST /reels/{reel_id}/regenerate     feedback-driven regeneration
POST /reels/{reel_id}/ask            follow-up question
POST /reels/{reel_id}/feedback       record feedback (no regeneration)
GET  /media/{key}                    local-storage media (S3 mode returns presigned URLs instead)
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import pipeline
from . import storage as st
from .config import settings
from .db import db, now_iso
from .models import AskRequest, FeedbackRequest, GenerateRequest, RegenerateRequest, TopicUpdateRequest
from .pdf.extract import page_count
from .worker import worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kavach.api")

app = FastAPI(title="Kavach API", version="0.1.0", description="Compress the delivery, not the knowledge.")
app.add_middleware(
    CORSMiddleware, allow_origins=settings.CORS_ORIGINS or ["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


def _media_url(key: str | None) -> str | None:
    return st.storage.url(key) if key else None


def _public_doc(doc: dict) -> dict:
    d = dict(doc)
    d.pop("error_trace", None)
    return d


def _public_reel(r: dict) -> dict:
    r = dict(r)
    r["video_url"] = _media_url(r.get("video_s3_key")) if r.get("status") == "COMPLETED" else None
    r["thumb_url"] = _media_url(r.get("thumb_s3_key")) if r.get("status") == "COMPLETED" else None
    r["sources_label"] = pipeline._fmt_pages(r.get("sources") or [])
    return r


@app.get("/health")
def health():
    return {"ok": True, "mode": settings.MODE, "storage": settings.STORAGE, "db": settings.DB,
            "brain": settings.BRAIN, "tts": settings.TTS, "model": settings.BEDROCK_MODEL}


# ---------------------------------------------------------------- documents
@app.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"PDF is larger than {settings.MAX_UPLOAD_MB} MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "This file does not look like a PDF.")
    doc_id = uuid.uuid4().hex[:12]
    tmp = settings.WORK_DIR / f"{doc_id}.pdf"
    tmp.write_bytes(data)
    try:
        n = page_count(tmp)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not open PDF: {e}")
    if n > settings.MAX_PAGES:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"PDF has {n} pages; the maximum is {settings.MAX_PAGES}.")
    st.storage.put_file(st.k_upload(doc_id), tmp, "application/pdf")
    tmp.unlink(missing_ok=True)
    doc = {
        "document_id": doc_id, "filename": file.filename, "page_count": n, "status": "UPLOADED",
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
    db.update_document(doc_id, status="GENERATING", progress="Queued", ai_enhanced=body.ai_enhanced)
    worker.submit(pipeline.generate_document, doc_id, body.ai_enhanced, body.topic_ids)
    return {"document_id": doc_id, "status": "GENERATING"}


@app.get("/documents/{doc_id}/reels")
def list_reels(doc_id: str):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    reels = [_public_reel(r) for r in db.list_reels(doc_id) if not r.get("hidden")]
    return {"document_id": doc_id, "status": doc.get("status"), "progress": doc.get("progress"), "reels": reels}


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
    if r.get("status") in ("PLANNING", "NARRATING", "RENDERING"):
        raise HTTPException(409, "Reel is already being generated")
    db.update_reel(reel_id, status="PLANNING", feedback=body.feedback)
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
def media(key: str):
    if settings.STORAGE != "local":
        return JSONResponse({"url": st.storage.url(key)})
    p: Path = st.storage.local_path(key)
    if not p.exists() or ".." in key:
        raise HTTPException(404, "Not found")
    return FileResponse(p)


# AWS Lambda entry point (container image or zip with Mangum)
try:
    from mangum import Mangum

    handler = Mangum(app)
except Exception:  # noqa: BLE001
    handler = None

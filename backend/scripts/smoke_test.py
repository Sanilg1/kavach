"""Run the whole pipeline in-process on the demo PDF (no HTTP server needed).
Works in local mode and is the quickest way to validate a real AWS configuration:

    python scripts/smoke_test.py                 # local: mock brain, local files
    KAVACH_MODE=aws python scripts/smoke_test.py # S3 + DynamoDB + Bedrock + Polly

Options: --pdf path  --topic <topic_id substring, default 'handshake'>  --all
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline, storage as st  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import db, now_iso  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(Path(__file__).resolve().parent.parent / "assets" / "demo_computer_networks.pdf"))
    ap.add_argument("--topic", default="handshake", help="substring of the topic id to generate (default: handshake)")
    ap.add_argument("--all", action="store_true", help="generate every recommended topic")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        from make_demo_pdf import build

        build(pdf)
    print(f"mode={settings.MODE} storage={settings.STORAGE} db={settings.DB} brain={settings.BRAIN} tts={settings.TTS} model={settings.BEDROCK_MODEL}")

    doc_id = uuid.uuid4().hex[:12]
    st.storage.put_file(st.k_upload(doc_id), pdf, "application/pdf")
    db.put_document({"document_id": doc_id, "filename": pdf.name, "page_count": 0, "status": "UPLOADED", "created_at": now_iso()})

    t0 = time.time()
    pipeline.analyze_document(doc_id)
    doc = db.get_document(doc_id)
    print(f"[analyze] {doc['status']} in {time.time() - t0:.1f}s -> {doc.get('title')!r}, quality={doc.get('quality')}, concepts={doc.get('concept_count')}")
    if doc["status"] != "READY":
        sys.exit(f"analysis failed: {doc.get('error')}")
    topics = db.list_topics(doc_id)
    for t in topics:
        print(f"   {t['learning_order']:>2}. {t['name']}  pages={t['source_pages']} shorts={t['estimated_shorts']} prereq={t['prerequisites']}")

    if args.all:
        chosen = [t["topic_id"] for t in topics if t.get("selected")]
    else:
        chosen = [t["topic_id"] for t in topics if args.topic.lower() in t["topic_id"]] or [topics[0]["topic_id"]]
    pipeline.apply_topic_update(doc_id, chosen, [], [])
    t0 = time.time()
    pipeline.generate_document(doc_id, ai_enhanced=False)
    doc = db.get_document(doc_id)
    print(f"[generate] {doc['status']} in {time.time() - t0:.1f}s")
    ok = True
    for r in db.list_reels(doc_id):
        print(f"   part {r['part']} {r['status']:<9} {r.get('title')!r} {r.get('duration', '')}s -> {r.get('video_s3_key') or r.get('error')}")
        if r["status"] == "COMPLETED":
            print(f"      url: {st.storage.url(r['video_s3_key'])}")
        else:
            ok = False
    print("document_id:", doc_id)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

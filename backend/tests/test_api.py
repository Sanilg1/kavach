import time

import pytest
from conftest import DEMO_PDF
from fastapi.testclient import TestClient

from app import main
from app.config import settings


@pytest.fixture(scope="module")
def api():
    with TestClient(main.app) as c:
        yield c


def _wait(api, doc_id, done, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = api.get(f"/documents/{doc_id}").json()
        if d["status"] in done:
            return d
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {done}, last status {d['status']}")


def test_upload_rejects_unsupported(api):
    r = api.post("/documents", files={"file": ("x.exe", b"MZ....", "application/octet-stream")})
    assert r.status_code == 400


def test_full_flow_and_race_guards(api):
    r = api.post("/documents", files={"file": ("notes.pdf", DEMO_PDF.read_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    doc_id = r.json()["document_id"]
    doc = _wait(api, doc_id, {"READY", "FAILED"})
    assert doc["status"] == "READY"
    assert doc["brain"] == "offline"

    topics = api.get(f"/documents/{doc_id}/topics").json()["topics"]
    hs = [t["topic_id"] for t in topics if "handshake" in t["topic_id"]]
    assert hs
    r = api.post(f"/documents/{doc_id}/topics", json={"selected_topic_ids": hs, "order": [], "added_topics": []})
    assert r.status_code == 200

    first = api.post(f"/documents/{doc_id}/generate", json={"language": "hinglish"})
    second = api.post(f"/documents/{doc_id}/generate", json={})
    assert first.status_code == 200
    assert second.status_code == 409          # double click must not start a second job

    doc = _wait(api, doc_id, {"COMPLETED", "FAILED"})
    assert doc["status"] == "COMPLETED"
    reels = api.get(f"/documents/{doc_id}/reels").json()["reels"]
    assert reels and all(r["status"] == "COMPLETED" for r in reels)
    assert all(r["language"] == "hinglish" for r in reels)
    assert all("download=" in r["download_url"] for r in reels)

    video = api.get(reels[0]["video_url"].replace(settings.PUBLIC_BASE_URL, ""))
    assert video.status_code == 200 and len(video.content) > 10_000

    ans = api.post(f"/reels/{reels[0]['reel_id']}/ask", json={"question": "What does the SYN carry?"})
    assert ans.status_code == 200 and "answer" in ans.json()

    notes = api.get(f"/documents/{doc_id}/notes.md")
    assert notes.status_code == 200 and notes.text.startswith("# ")


def test_rate_limit(api, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_HOUR", 2)
    main._hits.clear()
    hdr = {"x-forwarded-for": "203.0.113.9"}
    codes = [api.post("/documents/nope/generate", json={}, headers=hdr).status_code for _ in range(3)]
    assert codes[:2] == [404, 404] and codes[2] == 429
    main._hits.clear()

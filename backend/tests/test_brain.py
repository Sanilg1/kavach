import json

import pytest

from app.brain import client
from app.brain.client import BrainError, BrainUnavailable, FallbackBrain, _extract_json
from app.brain.planner import normalise_plan, validate_plan

TOPIC = {"name": "TCP", "topic_id": "tcp", "source_pages": [3]}


def test_extract_json_tolerates_fences_prose_and_trailing_commas():
    assert _extract_json('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert _extract_json('Sure! Here it is: {"x": 1} hope that helps') == {"x": 1}
    with pytest.raises(BrainError):
        _extract_json("no json here")


def test_normalise_plan_accepts_bare_array_and_fixes_numbering():
    raw = [
        {"part": 1, "title": "A", "scenes": [{"narration": "one", "elements": [{"type": "text", "text": "hi"}]}]},
        {"part": 3, "title": "B", "scenes": [{"narration": "two", "elements": []}]},
    ]
    plan = normalise_plan(raw, TOPIC, page_count=10)
    assert [p.part for p in plan.parts] == [1, 2]
    assert plan.parts[0].scenes[0].elements[0].type == "TEXT"


def test_normalise_plan_cleans_bad_fields():
    raw = {"parts": [{"title": "X", "sources": [3, 99, "x"], "scenes": [{"narration": "n", "elements": [
        {"type": "WAT", "text": 5, "x": 150, "y": -4, "animation": "SPIN"},
        {"type": "ARROW", "label": "SYN\\nseq=x", "from": "a", "to": "b"},
        {"type": "FLOW", "steps": ["a\\nb", "", "c"]},
    ]}], "quick_check": {"question": "q?", "options": ["a", "b"], "answer": 7}}]}
    part = normalise_plan(raw, TOPIC, page_count=10).parts[0]
    text, arrow, flow = part.scenes[0].elements
    assert text.type == "TEXT" and text.text == "5" and text.x == 100 and text.y == 0
    assert text.animation == "WRITE"
    assert arrow.label == "SYN\nseq=x"
    assert flow.steps == ["a b", "c"]
    assert part.sources == [3]
    assert part.quick_check.answer == 1


def test_validate_plan_flags_short_narration_and_missing_quiz():
    raw = {"parts": [{"title": "X", "scenes": [{"narration": "Too short.", "elements": [{"type": "TEXT", "text": "t"}]}]}]}
    issues = validate_plan(normalise_plan(raw, TOPIC, 10))
    assert any("words" in i for i in issues)
    assert any("quick_check" in i for i in issues)
    assert any("scene" in i for i in issues)


def test_validate_plan_accepts_good_plan():
    sentence = "The client sends a SYN segment with its initial sequence number to the server. "
    raw = {"parts": [{"title": "X", "scenes": [{"narration": sentence * 2, "elements": [{"type": "TEXT", "text": "t"}]}] * 3,
                      "quick_check": {"question": "q?", "options": ["a", "b", "c", "d"], "answer": 0}}]}
    assert validate_plan(normalise_plan(raw, TOPIC, 10)) == []


class _Raises:
    def __init__(self, exc):
        self.exc = exc

    def complete_json(self, *a, **k):
        raise self.exc


class _Returns:
    name = "offline"

    def complete_json(self, *a, **k):
        return {"ok": True}


def test_fallback_on_unavailable_only():
    chain = FallbackBrain(_Raises(BrainUnavailable("429")), _Returns(), "primary", cooldown=60)
    assert chain.complete_json("s", "u") == {"ok": True, "_brain": "offline"}
    assert chain.status()["effective"] == "offline"

    bug = FallbackBrain(_Raises(BrainError("bad request")), _Returns(), "primary", cooldown=60)
    with pytest.raises(BrainError):
        bug.complete_json("s", "u")


def test_groq_retry_after_parsing():
    g = client.GroqBrain(["k"])
    assert g._retry_after({"Retry-After": "3"}, {}) == 3.0
    msg = {"error": {"message": "Rate limit reached. Please try again in 1.5s."}}
    assert g._retry_after({}, msg) == 1.5
    msg = {"error": {"message": "Please try again in 250ms"}}
    assert g._retry_after({}, msg) == 0.25


@pytest.mark.parametrize("name", ["plan_dns.json", "plan_udp.json"])
def test_fixture_plans_normalise(name):
    from conftest import FIXTURES

    raw = json.load(open(FIXTURES / name, encoding="utf-8"))
    if isinstance(raw.get("part"), dict):
        raw = {"parts": [raw["part"]]}
    elif "scenes" in raw:
        raw = {"parts": [raw]}
    plan = normalise_plan(raw, TOPIC, 10)
    assert plan.parts and all(p.scenes for p in plan.parts)

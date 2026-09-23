import json

import pytest
from conftest import FIXTURES

from app.brain.planner import normalise_plan
from app.models import Element, PlanPart, Scene
from app.render.renderer import Renderer
from app.tts.synth import estimate_words

TOPIC = {"name": "T", "topic_id": "t", "source_pages": [1]}
ALL_FIXTURES = sorted(FIXTURES.glob("plan_*.json"))


def _parts(path):
    raw = json.load(open(path, encoding="utf-8"))
    if isinstance(raw.get("part"), dict):
        raw = {"parts": [raw["part"]]}
    elif "scenes" in raw:
        raw = {"parts": [raw]}
    return normalise_plan(raw, TOPIC, 999).parts


def _built(part):
    r = Renderer(720, 1280, 8)
    durs = [6.0] * len(part.scenes)
    r.build(part, durs, [estimate_words(s.narration, 5.5) for s in part.scenes])
    return r


def _visible_boxes(r: Renderer, scene_idx: int):
    """Unit-space bboxes of everything on the board at the end of a scene."""
    start = scene_idx
    while start > 0 and not r.scenes[start].clear:
        start -= 1
    out = []
    for st in r.scenes[start:scene_idx + 1]:
        for tm in st.elements:
            b = r._unit_bbox(tm.el)
            if b and tm.el.animation != "FADE_OUT":
                out.append((tm.el.id, b))
    return out


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=[p.stem for p in ALL_FIXTURES])
def test_real_model_plans_have_no_overlaps_and_stay_on_board(path):
    for part in _parts(path):
        r = _built(part)
        for si in range(len(r.scenes)):
            boxes = _visible_boxes(r, si)
            for i, (ida, a) in enumerate(boxes):
                assert a[0] >= 0 - 0.5 and a[2] <= 100 + 0.5, f"{ida} off the board horizontally: {a}"
                assert a[1] >= 0 - 0.5 and a[3] <= 100 + 0.5, f"{ida} off the board vertically: {a}"
                for idb, b in boxes[i + 1:]:
                    assert Renderer._overlap(a, b) <= 0.1, f"scene {si}: {ida} overlaps {idb}"


def test_every_primitive_and_devanagari_renders():
    els = [
        Element(id="t", type="TEXT", text="Title", size="title", x=50, y=8),
        Element(id="h", type="TEXT", text="नमस्ते दुनिया", x=50, y=20),
        Element(id="a", type="BOX", label="Client", x=25, y=32, w=24, h=7),
        Element(id="b", type="CIRCLE", label="Server", x=75, y=32, w=16),
        Element(id="ar", type="ARROW", label="SYN", **{"from": "a", "to": "b"}, animation="ARROW_FLOW"),
        Element(id="ln", type="LINE", x=10, y=42, x2=90, y2=42),
        Element(id="eq", type="EQUATION", text="RTT = 2 x delay", x=50, y=48),
        Element(id="fl", type="FLOW", steps=["one", "two", "three"], x=50, y=58, w=86, h=8),
        Element(id="tb", type="TABLE", rows=[["A", "B"], ["1", "2"]], x=50, y=70, w=60, h=12),
        Element(id="tl", type="TIMELINE", steps=["t0", "t1", "t2"], x=50, y=82, w=80),
        Element(id="hl", type="HIGHLIGHT", target="eq"),
        Element(id="im", type="IMAGE", page=1, x=80, y=48, w=10, h=8),
        Element(id="dg", type="DIAGRAM", nodes=[{"id": "r", "label": "Root", "x": 50, "y": 20}, {"id": "c", "label": "Child", "x": 50, "y": 40}],
                edges=[{"from": "r", "to": "c", "label": "delegates"}]),
    ]
    part = PlanPart(part=1, title="All", scenes=[Scene(narration="Everything at once.", elements=els)])
    r = _built(part)
    for t in (0.1, 2.0, r.total - 0.1):
        img = r.frame_at(t)
        assert img.size == (720, 1280)
        assert img.getbbox() is not None


def test_repeated_arrows_between_same_boxes_do_not_stack():
    els = [
        Element(id="c", type="BOX", label="Client", x=20, y=40, w=24, h=10),
        Element(id="s", type="BOX", label="Server", x=80, y=40, w=24, h=10),
        Element(id="1", type="ARROW", label="SYN", **{"from": "c", "to": "s"}),
        Element(id="2", type="ARROW", label="SYN-ACK", **{"from": "s", "to": "c"}),
        Element(id="3", type="ARROW", label="ACK", **{"from": "c", "to": "s"}),
    ]
    r = _built(PlanPart(part=1, title="x", scenes=[Scene(narration="n", elements=els)]))
    ys = [round(r._line_points(r._all[i])[0][1]) for i in ("1", "2", "3")]
    assert len(set(ys)) == 3 and ys == sorted(ys)   # three distinct lines, in chronological order

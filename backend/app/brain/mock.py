"""Offline, deterministic stand-in for Bedrock. It reads the same prompts the real
brain gets and builds plausible topic maps / teaching plans from the PDF text so the
whole pipeline (renderer, Polly/mock TTS, FFmpeg, frontend) can be exercised without
AWS credentials. Quality is intentionally basic - it exists to test the plumbing."""
from __future__ import annotations

import re
from collections import OrderedDict

_MINOR = {"and", "or", "of", "the", "versus", "vs", "in", "to", "a", "an", "for", "with", "on"}
_STOP = set(
    "the a an and or of to in on for with by is are was were be been this that these those it its as at from "
    "into which who whom whose what when where why how not no can may will shall should would could than then "
    "also such there their they them we you your our his her he she i if but so do does did has have had".split()
)


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if 30 <= len(p.strip()) <= 220]


def _pages(doc_text: str) -> list[tuple[int, str]]:
    out = []
    for m in re.finditer(r"\[Page (\d+)\]\n(.*?)(?=\n\[Page \d+\]\n|\Z)", doc_text, re.S):
        out.append((int(m.group(1)), m.group(2)))
    return out


def _headings(text: str) -> list[str]:
    heads = []
    for line in text.splitlines():
        s = line.strip()
        if 4 < len(s) < 60 and not s.endswith(".") and re.match(r"^(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Za-z0-9 ,:/&()\-]+$", s):
            core = re.sub(r"^\d+(?:\.\d+)*\s+", "", s)
            words = [w for w in core.split() if w.lower() not in _MINOR]
            if words and sum(1 for w in words if w[:1].isupper()) >= len(words) * 0.6:
                heads.append(core)
    return heads


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "topic"


def _keywords(text: str, n: int = 4, exclude: set[str] | None = None) -> list[str]:
    counts: dict[str, int] = {}
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text):
        lw = w.lower()
        if lw in _STOP or (exclude and lw in exclude):
            continue
        counts[lw] = counts.get(lw, 0) + 1
    ranked = sorted(counts, key=lambda k: (-counts[k], k))
    return [k.capitalize() for k in ranked[:n]]


class MockBrain:
    def complete_json(self, system: str, user: str, max_tokens: int | None = None, attachments=None) -> dict:
        if "TASK: Build the topic map" in system:
            return self._topic_map(user)
        if "TASK: Write the teaching plan" in system:
            return self._plan(user)
        if "TASK: Answer the student" in system:
            return self._ask(user)
        raise ValueError("MockBrain: unknown task")

    # ------------------------------------------------------------ topic map
    def _topic_map(self, user: str) -> dict:
        m = re.search(r"=== DOCUMENT TEXT ===\n(.*)\n=== END DOCUMENT ===", user, re.S)
        doc = m.group(1) if m else user
        title = re.search(r"Document title hint: (.*)", user)
        title = (title.group(1).strip() if title else "").replace("unknown", "") or "Study Material"
        pages = _pages(doc)

        found: "OrderedDict[str, list[int]]" = OrderedDict()
        for pno, text in pages:
            for h in _headings(text):
                key = h.strip()
                if key.lower() in title.lower() or key.lower() in ("contents", "table of contents", "references", "index"):
                    continue
                found.setdefault(key, [])
                if pno not in found[key]:
                    found[key].append(pno)
        heads = list(found.items())[:12]
        if len(heads) < 3:  # no usable headings: group pages
            heads = []
            step = max(1, len(pages) // 6) if pages else 1
            for i in range(0, len(pages), step):
                grp = pages[i : i + step]
                kws = _keywords(" ".join(t for _, t in grp), 2)
                heads.append((" & ".join(kws) or f"Pages {grp[0][0]}-{grp[-1][0]}", [p for p, _ in grp]))
        topics = []
        for i, (name, pgs) in enumerate(heads):
            pgs = sorted(set(pgs + [min(p + 1, len(pages)) for p in pgs]))[:4]
            rich = i % 3 == 1
            topics.append({
                "topic_id": _slug(name),
                "name": name,
                "description": f"Explain {name.lower()} as presented on page{'s' if len(pgs) > 1 else ''} {', '.join(map(str, pgs))}.",
                "source_pages": pgs,
                "prerequisites": [topics[-1]["topic_id"]] if topics and i % 2 == 1 else [],
                "difficulty": ["easy", "medium", "hard"][i % 3],
                "estimated_shorts": 2 if rich else 1,
                "estimated_duration": 90 if rich else 45,
                "learning_order": i + 1,
                "recommended": i < 9,
            })
        # de-duplicate ids
        seen = set()
        for t in topics:
            while t["topic_id"] in seen:
                t["topic_id"] += "-2"
            seen.add(t["topic_id"])
        return {
            "title": title,
            "summary": f"This document covers {len(topics)} main concepts across {len(pages)} pages. (Offline mock analysis - connect Bedrock for real understanding.)",
            "topics": topics,
            "uncertainties": [],
        }

    # ------------------------------------------------------------ teaching plan
    def _plan(self, user: str) -> dict:
        topic = re.search(r"TOPIC TO TEACH: (.*?) \(id=(.*?)\)", user)
        name = topic.group(1) if topic else "Topic"
        tid = topic.group(2) if topic else "topic"
        pages = re.search(r"Source pages: \[(.*?)\]", user)
        src = [int(x) for x in re.findall(r"\d+", pages.group(1))] if pages else [1]
        shorts = re.search(r"Suggested number of shorts: (\d+)", user)
        n_parts = max(1, min(3, int(shorts.group(1)))) if shorts else 1
        feedback = "REGENERATION REQUEST" in user
        excerpt = re.search(r"=== PDF EXCERPTS ===\n(.*)\n=== END EXCERPTS ===", user, re.S)
        text = excerpt.group(1) if excerpt else ""
        text = re.sub(r"\[Page \d+\]", " ", text)

        if re.search(r"handshake", name, re.I) and re.search(r"\bTCP\b", text or name):
            return _tcp_demo_plan(name, tid, src, feedback)

        sents = _sentences(text)
        name_words = {w.lower() for w in re.findall(r"[A-Za-z]+", name)}
        relevant = [s for s in sents if any(w in s.lower() for w in name_words)] or sents
        relevant = relevant[: 5 * n_parts] or [f"{name} is one of the key ideas in this material."]
        kws = _keywords(" ".join(relevant), 4, exclude=name_words) or ["Idea", "Detail", "Example", "Result"]

        parts = []
        for pi in range(n_parts):
            chunk = relevant[pi * 5 : (pi + 1) * 5] or relevant[:3]
            title = name if pi == 0 else f"{name}: part {pi + 1}"
            intro = "Let's take this slowly, step by step. " if feedback else ""
            scenes = [
                {
                    "narration": f"{intro}{name}. {chunk[0]}",
                    "clear": False,
                    "elements": [
                        {"id": "title", "type": "TEXT", "text": title, "size": "title", "x": 50, "y": 8, "animation": "WRITE"},
                        {"id": "sub", "type": "TEXT", "text": f"Source: pages {', '.join(map(str, src))}", "size": "small", "color": "grey", "x": 50, "y": 13, "animation": "FADE_IN"},
                    ],
                },
                {
                    "narration": chunk[1] if len(chunk) > 1 else f"There are {len(kws)} ideas to keep in mind here.",
                    "clear": False,
                    "elements": [
                        {"id": "flow", "type": "FLOW", "steps": kws[:4], "direction": "vertical", "x": 50, "y": 34, "w": 60, "h": 30, "color": "blue", "animation": "SEQUENTIAL_REVEAL"},
                    ],
                },
                {
                    "narration": chunk[2] if len(chunk) > 2 else "Notice how each idea builds on the previous one.",
                    "clear": False,
                    "elements": [
                        {"id": "d", "type": "DIAGRAM", "animation": "DRAW", "color": "ink",
                         "nodes": [{"id": "n1", "label": kws[0], "x": 28, "y": 62}, {"id": "n2", "label": kws[1 % len(kws)], "x": 72, "y": 72}, {"id": "n3", "label": kws[2 % len(kws)], "x": 40, "y": 80}],
                         "edges": [{"from": "n1", "to": "n2", "label": "leads to"}, {"from": "n2", "to": "n3", "label": "enables"}]},
                        {"id": "hl", "type": "HIGHLIGHT", "target": "flow", "animation": "HIGHLIGHT"},
                    ],
                },
                {
                    "narration": (chunk[3] if len(chunk) > 3 else f"To sum up: {kws[0]} and {kws[1 % len(kws)]} are the heart of {name}.") + " Check the source pages if anything is unclear.",
                    "clear": True,
                    "elements": [
                        {"id": "t2", "type": "TEXT", "text": "Key points", "size": "large", "x": 50, "y": 8, "animation": "WRITE"},
                        {"id": "tbl", "type": "TABLE", "rows": [["Idea", "Why it matters"]] + [[k, "see source pages"] for k in kws[:3]], "x": 50, "y": 36, "w": 88, "h": 30, "animation": "SEQUENTIAL_REVEAL"},
                        {"id": "eq", "type": "EQUATION", "text": f"{name} =\n{' + '.join(kws[:3])}", "x": 50, "y": 68, "color": "purple", "animation": "WRITE"},
                    ],
                },
            ]
            correct = chunk[0][:110].rstrip(".") + "."
            parts.append({
                "part": pi + 1,
                "title": title,
                "duration_target": 45,
                "visual_plan": ["Write the title", "Reveal the key ideas as a flow", "Sketch how the ideas connect", "Summarise in a table"],
                "scenes": scenes,
                "sources": src,
                "ai_added_context": [],
                "uncertainty": None,
                "quick_check": {
                    "question": f"According to the material, which statement about {name} is correct?",
                    "options": [correct, f"{name} is unrelated to {kws[0]}.", f"The material says {name} should be ignored during revision.", f"{name} is only mentioned in the references."],
                    "answer": 0,
                    "explanation": f"The source pages ({', '.join(map(str, src))}) state this directly.",
                },
            })
        return {"topic": name, "parts": parts}

    # ------------------------------------------------------------ ask
    def _ask(self, user: str) -> dict:
        q = re.search(r"STUDENT QUESTION: (.*)", user)
        q = q.group(1) if q else ""
        excerpt = re.search(r"=== PDF EXCERPTS ===\n(.*)\n=== END ===", user, re.S)
        text = excerpt.group(1) if excerpt else ""
        qwords = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", q)} - _STOP
        best: list[tuple[int, int, str]] = []
        for pno, ptxt in _pages(text):
            for s in _sentences(ptxt):
                score = sum(1 for w in qwords if w in s.lower())
                if score:
                    best.append((score, pno, s))
        best.sort(key=lambda x: -x[0])
        if not best:
            return {"answer": "The PDF excerpts for this lesson do not directly answer that question. (Offline mock brain - connect Bedrock for real answers.)", "sources": [], "ai_added_context": [], "uncertainty": None}
        top = best[:3]
        return {
            "answer": " ".join(s for _, _, s in top),
            "sources": sorted({p for _, p, _ in top}),
            "ai_added_context": [],
            "uncertainty": None,
        }


def _tcp_demo_plan(name: str, tid: str, src: list[int], feedback: bool) -> dict:
    """A hand-written, high-quality plan for the spec's ideal demo concept."""
    slow = "Let's slow down and take this one message at a time. " if feedback else ""
    return {
        "topic": name,
        "parts": [
            {
                "part": 1,
                "title": "Why TCP Needs a Handshake",
                "duration_target": 40,
                "visual_plan": ["Write title", "Draw client and server", "Show that both sides need agreed state", "Highlight the problem"],
                "scenes": [
                    {"narration": f"{slow}Before TCP can carry any application data, the two sides must agree that a connection exists and on where the byte stream starts.", "clear": False,
                     "elements": [
                         {"id": "title", "type": "TEXT", "text": "Why TCP needs a handshake", "size": "title", "x": 50, "y": 8, "animation": "WRITE"},
                         {"id": "client", "type": "BOX", "label": "Client", "x": 50, "y": 24, "w": 40, "h": 8, "color": "blue", "animation": "DRAW"},
                         {"id": "server", "type": "BOX", "label": "Server", "x": 50, "y": 58, "w": 40, "h": 8, "color": "green", "animation": "DRAW"},
                     ]},
                    {"narration": "TCP is reliable and ordered, so each side keeps state: sequence numbers, buffers, and window sizes. That state has to be set up before the first byte is sent.", "clear": False,
                     "elements": [
                         {"id": "cs", "type": "TEXT", "text": "seq #, buffers, window", "size": "small", "color": "blue", "x": 50, "y": 31, "animation": "WRITE"},
                         {"id": "ss", "type": "TEXT", "text": "seq #, buffers, window", "size": "small", "color": "green", "x": 50, "y": 65, "animation": "WRITE"},
                         {"id": "q", "type": "TEXT", "text": "How do both sides agree?", "size": "large", "color": "red", "x": 50, "y": 78, "animation": "FADE_IN"},
                     ]},
                    {"narration": "A single request is not enough, because the client would never know whether the server actually received it. So TCP uses an exchange of three messages: the three-way handshake.", "clear": False,
                     "elements": [
                         {"id": "a1", "type": "ARROW", "x": 50, "y": 35, "x2": 50, "y2": 53, "label": "request?", "color": "grey", "animation": "ARROW_FLOW"},
                         {"id": "hl", "type": "HIGHLIGHT", "target": "q", "animation": "HIGHLIGHT"},
                     ]},
                ],
                "sources": src,
                "ai_added_context": [],
                "uncertainty": None,
                "quick_check": {
                    "question": "Why is a single connection request message not enough to start a TCP connection?",
                    "options": ["The client cannot know whether the server received it", "TCP messages are too small to carry a request", "The server must first resolve the client's DNS name", "Encryption keys must be exchanged first"],
                    "answer": 0,
                    "explanation": "Without a reply, the client has no confirmation that the server is ready and has set up its connection state.",
                },
            },
            {
                "part": 2,
                "title": "SYN, SYN-ACK, ACK",
                "duration_target": 50,
                "visual_plan": ["Draw client and server", "Animate SYN", "Animate SYN-ACK", "Animate ACK", "Show connection established"],
                "scenes": [
                    {"narration": f"{slow}Here is the handshake itself. The client starts by sending a SYN segment, which carries its initial sequence number.", "clear": True,
                     "elements": [
                         {"id": "title", "type": "TEXT", "text": "The three-way handshake", "size": "title", "x": 50, "y": 8, "animation": "WRITE"},
                         {"id": "client", "type": "BOX", "label": "Client", "x": 22, "y": 16, "w": 28, "h": 6, "color": "blue", "animation": "DRAW"},
                         {"id": "server", "type": "BOX", "label": "Server", "x": 78, "y": 16, "w": 28, "h": 6, "color": "green", "animation": "DRAW"},
                         {"id": "lc", "type": "LINE", "x": 22, "y": 20, "x2": 22, "y2": 74, "color": "grey", "animation": "DRAW"},
                         {"id": "ls", "type": "LINE", "x": 78, "y": 20, "x2": 78, "y2": 74, "color": "grey", "animation": "DRAW"},
                         {"id": "syn", "type": "ARROW", "x": 24, "y": 26, "x2": 76, "y2": 36, "label": "1. SYN (seq = x)", "color": "blue", "animation": "ARROW_FLOW"},
                     ]},
                    {"narration": "The server replies with SYN-ACK. It acknowledges the client's sequence number by sending x plus one, and includes its own initial sequence number y.", "clear": False,
                     "elements": [
                         {"id": "synack", "type": "ARROW", "x": 76, "y": 42, "x2": 24, "y2": 52, "label": "2. SYN-ACK (seq = y, ack = x+1)", "color": "green", "animation": "ARROW_FLOW"},
                     ]},
                    {"narration": "Finally the client sends an ACK with y plus one. Now both sides know the other one is alive and has agreed on the starting numbers. The connection is established.", "clear": False,
                     "elements": [
                         {"id": "ack", "type": "ARROW", "x": 24, "y": 58, "x2": 76, "y2": 68, "label": "3. ACK (ack = y+1)", "color": "blue", "animation": "ARROW_FLOW"},
                         {"id": "est", "type": "TEXT", "text": "Connection established", "size": "large", "color": "green", "x": 50, "y": 82, "animation": "WRITE"},
                         {"id": "hl", "type": "HIGHLIGHT", "target": "est", "animation": "HIGHLIGHT"},
                     ]},
                    {"narration": "Remember the pattern: SYN, SYN-ACK, ACK. Three messages, and each side has both sent and received an acknowledgement.", "clear": True,
                     "elements": [
                         {"id": "t2", "type": "TEXT", "text": "Summary", "size": "title", "x": 50, "y": 8, "animation": "WRITE"},
                         {"id": "flow", "type": "FLOW", "steps": ["SYN", "SYN-ACK", "ACK", "Established"], "direction": "vertical", "x": 50, "y": 32, "w": 56, "h": 34, "color": "purple", "animation": "SEQUENTIAL_REVEAL"},
                         {"id": "tbl", "type": "TABLE", "rows": [["Message", "From", "Carries"], ["SYN", "Client", "seq = x"], ["SYN-ACK", "Server", "seq = y, ack = x+1"], ["ACK", "Client", "ack = y+1"]], "x": 50, "y": 70, "w": 90, "h": 24, "animation": "SEQUENTIAL_REVEAL"},
                     ]},
                ],
                "sources": src,
                "ai_added_context": [],
                "uncertainty": None,
                "quick_check": {
                    "question": "What does the third message (ACK) of the handshake accomplish?",
                    "options": ["Confirms receipt of the server's SYN-ACK so both sides have agreed state", "Encrypts the connection", "Resolves the server's IP address", "Increases the packet size"],
                    "answer": 0,
                    "explanation": "The final ACK tells the server that the client received its SYN-ACK; after it, both sides have exchanged and acknowledged initial sequence numbers.",
                },
            },
        ],
    }

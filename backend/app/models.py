"""Pydantic models: API payloads + the Teaching Plan JSON contract between the
Brain AI and the whiteboard renderer."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

DocStatus = Literal[
    "UPLOADED", "PROCESSING", "ANALYZING", "READY", "GENERATING", "COMPLETED", "FAILED"
]
ReelStatus = Literal["PENDING", "PLANNING", "NARRATING", "RENDERING", "COMPLETED", "FAILED"]

PRIMITIVES = [
    "TEXT", "BOX", "CIRCLE", "LINE", "ARROW", "DIAGRAM", "EQUATION",
    "TABLE", "HIGHLIGHT", "IMAGE", "FLOW", "TIMELINE",
]
ANIMATIONS = [
    "DRAW", "WRITE", "FADE_IN", "FADE_OUT", "MOVE", "HIGHLIGHT",
    "ARROW_FLOW", "SEQUENTIAL_REVEAL",
]


# ---------------------------------------------------------------- suitability
class Suitability(BaseModel):
    suitable: bool
    page_count: int
    quality: Literal["good", "fair", "poor"]
    warnings: list[str] = []
    recommendations: list[str] = []
    ocr_pages: list[int] = []


# ---------------------------------------------------------------- topic map
class Topic(BaseModel):
    topic_id: str
    name: str
    description: str = ""
    source_pages: list[int] = []
    prerequisites: list[str] = []          # topic_ids
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    estimated_shorts: int = 1
    estimated_duration: int = 45           # seconds, all shorts combined
    learning_order: int = 0
    recommended: bool = True
    selected: bool = True
    user_added: bool = False


class Uncertainty(BaseModel):
    uncertainty: bool = True
    reason: str
    source_pages: list[int] = []


class TopicMap(BaseModel):
    brain: str = ""                        # which brain produced it (bedrock:…, groq:…, offline)
    title: str
    summary: str = ""
    topics: list[Topic]
    uncertainties: list[Uncertainty] = []


# ---------------------------------------------------------------- teaching plan (renderer contract)
class Element(BaseModel):
    """One visual primitive on the whiteboard. Coordinates are in a 0-100 space
    (x,y = center). Structured primitives (FLOW/TABLE/DIAGRAM/TIMELINE) lay out
    their own children inside their bounding box."""
    id: str = ""
    type: str = "TEXT"
    animation: str = "FADE_IN"
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    text: str = ""                      # TEXT / EQUATION / labels
    label: str = ""                     # BOX / CIRCLE / ARROW label
    size: Literal["title", "large", "normal", "small"] = "normal"
    color: str = "ink"                  # ink | blue | red | green | orange | purple | grey
    # LINE / ARROW: either from/to element ids or explicit points
    from_id: str = Field(default="", alias="from")
    to_id: str = Field(default="", alias="to")
    x2: Optional[float] = None
    y2: Optional[float] = None
    # FLOW / TIMELINE
    steps: list[str] = []
    direction: Literal["horizontal", "vertical"] = "horizontal"
    # TABLE
    rows: list[list[str]] = []
    # DIAGRAM
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    # HIGHLIGHT
    target: str = ""
    # IMAGE: source page number to show a thumbnail of
    page: Optional[int] = None
    # MOVE: destination
    to_x: Optional[float] = None
    to_y: Optional[float] = None

    model_config = {"populate_by_name": True}


class Scene(BaseModel):
    scene_id: str = ""
    narration: str
    clear: bool = False                 # wipe the board before this scene
    elements: list[Element] = []


class QuickCheck(BaseModel):
    question: str
    options: list[str]
    answer: int
    explanation: str = ""


class PlanPart(BaseModel):
    part: int
    title: str
    duration_target: int = 45
    script: str = ""
    visual_plan: list[str] = []
    scenes: list[Scene] = []
    sources: list[int] = []
    ai_added_context: list[str] = []
    uncertainty: Optional[Uncertainty] = None
    quick_check: Optional[QuickCheck] = None


class TeachingPlan(BaseModel):
    brain: str = ""
    topic: str
    topic_id: str = ""
    learning_order: int = 0
    parts: list[PlanPart]
    quick_check: Optional[QuickCheck] = None


# ---------------------------------------------------------------- API payloads
class TopicUpdateRequest(BaseModel):
    selected_topic_ids: list[str] = []
    order: list[str] = []                       # optional explicit order of topic_ids
    added_topics: list[dict[str, Any]] = []     # {"name": "...", "source_pages": [..]}


Language = Literal["en", "en-IN", "hinglish", "hi"]


class GenerateRequest(BaseModel):
    ai_enhanced: bool = False
    topic_ids: Optional[list[str]] = None       # default: all selected topics
    language: Language = "en"                   # narration language / voice


class AskRequest(BaseModel):
    question: str
    ai_enhanced: bool = False


class RegenerateRequest(BaseModel):
    feedback: Literal["good", "didnt_understand", "too_fast", "too_difficult", "explain_differently"]
    ai_enhanced: bool = False


class FeedbackRequest(BaseModel):
    feedback: str
    note: str = ""

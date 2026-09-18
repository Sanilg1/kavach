"""LLM client abstraction for the Brain AI.

    converse - Amazon Bedrock Converse API through boto3 (default; inference-profile ids
               like "global.anthropic.claude-opus-4-6-v1")
    bedrock  - Claude in Amazon Bedrock through the Anthropic SDK's Mantle client
               (model ids like "anthropic.claude-opus-5"; needs Mantle access on the account)
    mock     - deterministic offline brain (see mock.py) for local development
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..config import settings

log = logging.getLogger("kavach.brain")


class BrainError(RuntimeError):
    pass


# attachments: [{"kind": "pdf", "bytes": b, "name": "study-material"} | {"kind": "image", "bytes": b, "format": "jpeg"}]
Attachment = dict[str, Any]


def _attachment_error(msg: str) -> bool:
    msg = msg.lower()
    return any(k in msg for k in ("document", "image", "media", "content block", "too large", "input is too long"))


def _extract_json(text: str) -> Any:
    """Parse a JSON object from model output, tolerating fences and stray prose."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        candidate = t[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # remove trailing commas before } or ]
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
            return json.loads(candidate)
    raise BrainError("Model did not return a JSON object")


class BedrockMantleBrain:
    def __init__(self):
        from anthropic import AnthropicBedrockMantle

        self.client = AnthropicBedrockMantle(aws_region=settings.AWS_REGION, timeout=600, max_retries=2)
        self.model = settings.BEDROCK_MODEL
        self._effort_supported = True

    @staticmethod
    def _content(user: str, attachments: list[Attachment] | None) -> list[dict] | str:
        if not attachments:
            return user
        import base64

        blocks: list[dict] = []
        for a in attachments:
            data = base64.b64encode(a["bytes"]).decode("ascii")
            if a["kind"] == "pdf":
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}})
            else:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": f"image/{a.get('format', 'jpeg')}", "data": data}})
        blocks.append({"type": "text", "text": user})
        return blocks

    def complete_json(self, system: str, user: str, max_tokens: int | None = None,
                      attachments: list[Attachment] | None = None) -> dict:
        from anthropic import APIStatusError

        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens or settings.BRAIN_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": self._content(user, attachments)}],
            thinking={"type": "adaptive"},
        )
        if self._effort_supported and settings.BRAIN_EFFORT:
            kwargs["output_config"] = {"effort": settings.BRAIN_EFFORT}
        try:
            text = self._stream(kwargs)
        except APIStatusError as e:
            msg = str(e).lower()
            if attachments and _attachment_error(msg):
                log.warning("Bedrock rejected attachments, retrying text-only: %s", e)
                kwargs["messages"] = [{"role": "user", "content": user}]
                text = self._stream(kwargs)
            elif "output_config" in msg or "effort" in msg or "thinking" in msg:
                # older Bedrock deployments reject these fields; retry without them
                log.warning("Bedrock rejected effort/thinking config, retrying without: %s", e)
                self._effort_supported = False
                kwargs.pop("output_config", None)
                kwargs.pop("thinking", None)
                text = self._stream(kwargs)
            else:
                raise BrainError(f"Bedrock error: {e}") from e
        return _extract_json(text)

    def _stream(self, kwargs: dict) -> str:
        with self.client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise BrainError("The model declined this request (refusal).")
        if msg.stop_reason == "max_tokens":
            raise BrainError("The model output was truncated (max_tokens). Increase KAVACH_BRAIN_MAX_TOKENS.")
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


class BedrockConverseBrain:
    """Amazon Bedrock Converse API (boto3). Works with inference-profile ids such as
    global.anthropic.claude-opus-4-6-v1 and streams so long teaching plans do not hit
    HTTP timeouts. Adaptive thinking is requested when the model supports it."""

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.client = boto3.client(
            "bedrock-runtime", region_name=settings.AWS_REGION,
            config=Config(read_timeout=900, connect_timeout=30, retries={"max_attempts": 3, "mode": "adaptive"}),
        )
        self.model = settings.BEDROCK_MODEL
        self._thinking_supported = True

    @staticmethod
    def _content(user: str, attachments: list[Attachment] | None) -> list[dict]:
        blocks: list[dict] = []
        for a in attachments or []:
            if a["kind"] == "pdf":
                blocks.append({"document": {"format": "pdf", "name": a.get("name", "study-material"), "source": {"bytes": a["bytes"]}}})
            else:
                blocks.append({"image": {"format": a.get("format", "jpeg"), "source": {"bytes": a["bytes"]}}})
        blocks.append({"text": user})
        return blocks

    def complete_json(self, system: str, user: str, max_tokens: int | None = None,
                      attachments: list[Attachment] | None = None) -> dict:
        from botocore.exceptions import ClientError

        kwargs: dict[str, Any] = dict(
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": self._content(user, attachments)}],
            inferenceConfig={"maxTokens": min(max_tokens or settings.BRAIN_MAX_TOKENS, 64000)},
        )
        if self._thinking_supported:
            kwargs["additionalModelRequestFields"] = {"thinking": {"type": "adaptive"}}
        else:
            kwargs["inferenceConfig"]["temperature"] = 0.3
        try:
            text, stop = self._stream(kwargs)
        except ClientError as e:
            msg = str(e).lower()
            if attachments and _attachment_error(msg):
                log.warning("Bedrock rejected attachments, retrying text-only: %s", e)
                kwargs["messages"] = [{"role": "user", "content": [{"text": user}]}]
                text, stop = self._stream(kwargs)
            elif self._thinking_supported and ("thinking" in msg or "additionalmodelrequestfields" in msg or "validationexception" in msg):
                log.warning("Bedrock rejected adaptive thinking for %s, retrying without: %s", self.model, e)
                self._thinking_supported = False
                kwargs.pop("additionalModelRequestFields", None)
                kwargs["inferenceConfig"]["temperature"] = 0.3
                text, stop = self._stream(kwargs)
            else:
                raise BrainError(f"Bedrock error: {e}") from e
        if stop == "max_tokens":
            raise BrainError("The model output was truncated (max_tokens). Increase KAVACH_BRAIN_MAX_TOKENS.")
        return _extract_json(text)

    def _stream(self, kwargs: dict) -> tuple[str, str]:
        resp = self.client.converse_stream(**kwargs)
        parts: list[str] = []
        stop = ""
        for ev in resp["stream"]:
            if "contentBlockDelta" in ev:
                delta = ev["contentBlockDelta"]["delta"]
                if "text" in delta:
                    parts.append(delta["text"])
            elif "messageStop" in ev:
                stop = ev["messageStop"].get("stopReason", "")
            elif "metadata" in ev:
                usage = ev["metadata"].get("usage", {})
                log.info("bedrock usage in=%s out=%s", usage.get("inputTokens"), usage.get("outputTokens"))
        return "".join(parts), stop


def _build():
    kind = settings.BRAIN
    if kind == "bedrock":
        return BedrockMantleBrain()
    if kind == "converse":
        return BedrockConverseBrain()
    from .mock import MockBrain

    return MockBrain()


brain = _build()


def complete_json_with_retry(system: str, user: str, max_tokens: int | None = None, attempts: int = 2,
                             attachments: list[Attachment] | None = None) -> dict:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return brain.complete_json(system, user, max_tokens, attachments=attachments)
        except (json.JSONDecodeError, BrainError) as e:  # retry malformed JSON once
            last = e
            log.warning("brain attempt %d failed: %s", i + 1, e)
            if isinstance(e, BrainError) and "refusal" in str(e):
                break
    raise BrainError(f"Brain AI failed: {last}")

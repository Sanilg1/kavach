"""LLM client abstraction for the Brain AI.

    bedrock  - Claude in Amazon Bedrock through the Anthropic SDK's Mantle client
               (model ids like "anthropic.claude-opus-5")
    converse - legacy Bedrock runtime Converse API through boto3
               (model ids like "anthropic.claude-3-5-sonnet-20241022-v2:0")
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

    def complete_json(self, system: str, user: str, max_tokens: int | None = None) -> dict:
        from anthropic import APIStatusError

        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens or settings.BRAIN_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
        )
        if self._effort_supported and settings.BRAIN_EFFORT:
            kwargs["output_config"] = {"effort": settings.BRAIN_EFFORT}
        try:
            text = self._stream(kwargs)
        except APIStatusError as e:
            msg = str(e).lower()
            if "output_config" in msg or "effort" in msg or "thinking" in msg:
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
    def __init__(self):
        import boto3

        self.client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
        self.model = settings.BEDROCK_MODEL

    def complete_json(self, system: str, user: str, max_tokens: int | None = None) -> dict:
        resp = self.client.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": min(max_tokens or settings.BRAIN_MAX_TOKENS, 32000), "temperature": 0.3},
        )
        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in blocks)
        if resp.get("stopReason") == "max_tokens":
            raise BrainError("The model output was truncated (max_tokens).")
        return _extract_json(text)


def _build():
    kind = settings.BRAIN
    if kind == "bedrock":
        return BedrockMantleBrain()
    if kind == "converse":
        return BedrockConverseBrain()
    from .mock import MockBrain

    return MockBrain()


brain = _build()


def complete_json_with_retry(system: str, user: str, max_tokens: int | None = None, attempts: int = 2) -> dict:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return brain.complete_json(system, user, max_tokens)
        except (json.JSONDecodeError, BrainError) as e:  # retry malformed JSON once
            last = e
            log.warning("brain attempt %d failed: %s", i + 1, e)
            if isinstance(e, BrainError) and "refusal" in str(e):
                break
    raise BrainError(f"Brain AI failed: {last}")

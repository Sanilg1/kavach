"""LLM client abstraction for the Brain AI.

    converse - Amazon Bedrock Converse API through boto3 (default; inference-profile ids
               like "global.anthropic.claude-opus-4-6-v1")
    bedrock  - Claude in Amazon Bedrock through the Anthropic SDK's Mantle client
               (model ids like "anthropic.claude-opus-5"; needs Mantle access on the account)
    anthropic- first-party Claude API through the Anthropic SDK (ANTHROPIC_API_KEY);
               stopgap when Bedrock quotas are not yet available on an account
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
    """Anthropic SDK Messages API: Claude in Amazon Bedrock (Mantle) by default, or the
    first-party Claude API when KAVACH_BRAIN=anthropic (stopgap while Bedrock quotas are
    being raised; needs ANTHROPIC_API_KEY)."""

    def __init__(self, first_party: bool = False):
        if first_party:
            from anthropic import Anthropic

            self.client = Anthropic(timeout=600, max_retries=2)
            self.model = settings.ANTHROPIC_MODEL
        else:
            from anthropic import AnthropicBedrockMantle

            self.client = AnthropicBedrockMantle(aws_region=settings.BEDROCK_REGION, timeout=600, max_retries=2)
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


class GroqBrain:
    """OpenAI-compatible chat completions on Groq (stopgap while Bedrock is quota-limited).
    Text-only: attachments are dropped. JSON mode keeps the plan contract intact."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = settings.GROQ_MODEL
        self.url = "https://api.groq.com/openai/v1/chat/completions"

    def complete_json(self, system: str, user: str, max_tokens: int | None = None,
                      attachments: list[Attachment] | None = None) -> dict:
        import urllib.error
        import urllib.request

        if attachments:
            user += "\n\n(Attachments were omitted for this model - rely on the extracted text.)"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_completion_tokens": min(max_tokens or settings.BRAIN_MAX_TOKENS, 32000),
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "kavach/0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:400]
            raise BrainError(f"Groq error {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise BrainError(f"Groq connection error: {e}") from e
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise BrainError("The model output was truncated (max_tokens).")
        usage = data.get("usage", {})
        log.info("groq usage in=%s out=%s", usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return _extract_json(choice.get("message", {}).get("content") or "")


class BedrockConverseBrain:
    """Amazon Bedrock Converse API (boto3). Works with inference-profile ids such as
    global.anthropic.claude-opus-4-6-v1 and streams so long teaching plans do not hit
    HTTP timeouts. Adaptive thinking is requested when the model supports it."""

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.client = boto3.client(
            "bedrock-runtime", region_name=settings.BEDROCK_REGION,
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


_QUOTA_MARKERS = ("too many tokens", "too many requests", "throttlingexception", "use case details", "groq error 429",
                  "groq error 401", "groq error 403", "groq connection error",
                  "not available for this account", "accessdeniedexception", "serviceunavailable",
                  "rate_limit", "overloaded", "credit balance", "authentication_error", "invalid x-api-key",
                  "permission_error", "billing")


def _is_quota_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(m in msg for m in _QUOTA_MARKERS)


class FallbackBrain:
    """Try `primary`; if it is quota-limited, answer with `fallback` instead and remember
    it for a cooldown so the app stays responsive. Fallbacks can be chained
    (Bedrock -> Anthropic API -> offline). As soon as the primary answers again it is
    used - no redeploy needed."""

    def __init__(self, primary, fallback, primary_name: str, cooldown: float):
        import time

        self._time = time
        self.primary, self.fallback = primary, fallback
        self.primary_name = primary_name
        self.fallback_name = getattr(fallback, "primary_name", None) or getattr(fallback, "name", "offline")
        self.cooldown = cooldown
        self.blocked_until = 0.0
        self.last_error = ""
        self.last_used = primary_name

    def complete_json(self, system: str, user: str, max_tokens: int | None = None, attachments=None) -> dict:
        if self._time.time() >= self.blocked_until:
            try:
                out = self.primary.complete_json(system, user, max_tokens, attachments=attachments)
                self.last_used = self.primary_name
                self.last_error = ""
                return out
            except Exception as e:  # noqa: BLE001
                if not _is_quota_error(e):
                    raise
                self.last_error = str(e)[:300]
                self.blocked_until = self._time.time() + self.cooldown
                log.warning("%s unavailable (%s); using %s for %.0f s", self.primary_name, self.last_error[:120],
                            self.fallback_name, self.cooldown)
        out = self.fallback.complete_json(system, user, max_tokens, attachments=attachments)
        self.last_used = getattr(self.fallback, "last_used", self.fallback_name)
        return out

    def status(self) -> dict:
        blocked = self._time.time() < self.blocked_until
        inner = self.fallback.status() if isinstance(self.fallback, FallbackBrain) else None
        effective = (inner["effective"] if inner else self.fallback_name) if blocked else self.primary_name
        return {
            "configured": self.primary_name,
            "effective": effective,
            "last_used": self.last_used,
            "retry_in_s": max(0, int(self.blocked_until - self._time.time())) if blocked else 0,
            "last_error": self.last_error,
            "chain": [self.primary_name] + (inner["chain"] if inner else [self.fallback_name]),
        }


def _build():
    import os

    from .mock import MockBrain

    kind = settings.BRAIN
    mock = MockBrain()
    mock.name = "offline"
    if kind == "mock":
        return mock
    # optional middle tiers, used while Bedrock is quota-limited: Anthropic API, then Groq
    tail = mock
    if kind != "groq" and os.environ.get("GROQ_API_KEY") and settings.BRAIN_FALLBACK:
        tail = FallbackBrain(GroqBrain(os.environ["GROQ_API_KEY"]), tail, f"groq:{settings.GROQ_MODEL}", settings.BRAIN_COOLDOWN)
    if kind != "anthropic" and os.environ.get("ANTHROPIC_API_KEY") and settings.BRAIN_FALLBACK:
        tail = FallbackBrain(BedrockMantleBrain(first_party=True), tail, f"anthropic:{settings.ANTHROPIC_MODEL}", settings.BRAIN_COOLDOWN)
    if kind == "groq":
        return FallbackBrain(GroqBrain(os.environ.get("GROQ_API_KEY", "")), mock, f"groq:{settings.GROQ_MODEL}", settings.BRAIN_COOLDOWN)
    if kind == "bedrock":
        primary, name = BedrockMantleBrain(), f"bedrock:{settings.BEDROCK_MODEL}"
    elif kind == "anthropic":
        primary, name = BedrockMantleBrain(first_party=True), f"anthropic:{settings.ANTHROPIC_MODEL}"
    else:
        primary, name = BedrockConverseBrain(), f"bedrock:{settings.BEDROCK_MODEL}"
    if settings.BRAIN_FALLBACK:
        return FallbackBrain(primary, tail, name, settings.BRAIN_COOLDOWN)
    return primary


brain = _build()


def brain_status() -> dict:
    if isinstance(brain, FallbackBrain):
        return brain.status()
    name = "offline" if settings.BRAIN == "mock" else f"{settings.BRAIN}:{settings.BEDROCK_MODEL}"
    return {"configured": name, "effective": name, "last_used": name, "retry_in_s": 0, "last_error": ""}


def brain_used() -> str:
    """Name of the brain that produced the most recent answer."""
    return getattr(brain, "last_used", brain_status()["effective"])


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

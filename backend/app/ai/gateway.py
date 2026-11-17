"""The one door to models: every AI or ML output goes through here and is recorded.

AI recommends · rules enforce · humans decide. Nothing produced here changes a policy outcome, approves or
rejects a step, awards a quote, approves a payment or exports a bank file. Callers may only show
suggestions, add advisory flags or quote cited sources. Every output is stored in `ai_predictions` with the
model, its version, a reference to the input, the input's SHA-256, latency and token counts
(tests/unit/test_ai_boundaries.py keeps the policy engine free of any AI import).

Language model providers, chosen by PROCURAX_LLM_PROVIDER:

* `replay` (default): answers only from recordings in PROCURAX_LLM_REPLAY_DIR, keyed by the SHA-256 of the
  exact prompt; anything else is a miss and callers fall back to their deterministic path. No network.
* `anthropic`: Claude through the official SDK (credentials from ANTHROPIC_API_KEY or an `ant` profile),
  with the server-side refusal fallback enabled. With PROCURAX_LLM_RECORD=true every answer is also written
  to the replay directory, so CI and demos can replay it without a key. Costs money: off by default.
"""

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIPrediction
from app.core.config import Settings, get_settings
from app.core.db import utcnow


def sha256_of(data: object) -> str:
    body = (
        data if isinstance(data, str) else json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def record(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    kind: str,
    model: str,
    model_version: str,
    input_ref: str,
    input_data: object,
    output: dict,
    latency_ms: float,
    actor_user_id: uuid.UUID | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> AIPrediction:
    """Store one model output. Insert-only for the runtime role; never updated."""
    row = AIPrediction(
        org_id=org_id,
        kind=kind,
        model=model,
        model_version=model_version,
        input_ref=input_ref,
        input_sha256=sha256_of(input_data),
        output=output,
        latency_ms=round(latency_ms, 2),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        actor_user_id=actor_user_id,
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    model_version: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    stop_reason: str | None


class LLMUnavailable(RuntimeError):
    """No answer: replay miss, provider off or down, or a refusal. Callers use their deterministic path."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class LLMProvider(Protocol):
    name: str

    async def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> LLMResult: ...


def prompt_key(system: str, prompt: str) -> str:
    return sha256_of(f"{system}\n\n{prompt}")


class ReplayProvider:
    name = "replay"

    def __init__(self, directory: str | None) -> None:
        self.directory = Path(directory) if directory else None

    async def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> LLMResult:
        start = time.perf_counter()
        key = prompt_key(system, prompt)
        path = self.directory / f"{key}.json" if self.directory else None
        if path is None or not path.is_file():
            raise LLMUnavailable("llm_replay_miss", "No recorded answer for this prompt")
        recording = json.loads(path.read_text(encoding="utf-8"))
        return LLMResult(
            text=recording["text"],
            model="replay",
            model_version=f"{recording.get('model', 'unknown')}@{recording.get('recorded_at', '?')}",
            input_tokens=recording.get("input_tokens"),
            output_tokens=recording.get("output_tokens"),
            latency_ms=(time.perf_counter() - start) * 1000,
            stop_reason=recording.get("stop_reason"),
        )


class AnthropicProvider:
    """Claude via the official SDK; refusal fallback on; stable system prompt cached."""

    name = "anthropic"

    def __init__(self, settings: Settings, client: object | None = None) -> None:
        self.settings = settings
        if client is None:
            import anthropic  # noqa: PLC0415 - only needed when this provider is configured

            client = anthropic.AsyncAnthropic(timeout=settings.llm_timeout_seconds, max_retries=2)
        self.client = client

    async def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> LLMResult:
        import anthropic  # noqa: PLC0415

        settings = self.settings
        start = time.perf_counter()
        try:
            response = await self.client.beta.messages.create(  # type: ignore[attr-defined]
                model=settings.llm_model,
                max_tokens=max_tokens or settings.llm_max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": settings.llm_effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable("llm_rate_limited", "The language model is rate-limited") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable("llm_error", f"The language model returned {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable("llm_unreachable", "The language model could not be reached") from exc
        if response.stop_reason == "refusal":
            raise LLMUnavailable("llm_refused", "The language model declined this request")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        result = LLMResult(
            text=text,
            model=response.model,
            model_version=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=(time.perf_counter() - start) * 1000,
            stop_reason=response.stop_reason,
        )
        if settings.llm_record and settings.llm_replay_dir:
            recording = {
                "text": text,
                "model": response.model,
                "recorded_at": utcnow().isoformat(timespec="seconds"),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "stop_reason": response.stop_reason,
            }
            await asyncio.to_thread(
                _save_recording, settings.llm_replay_dir, prompt_key(system, prompt), recording
            )
        return result


def _save_recording(directory: str, key: str, recording: dict) -> None:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{key}.json").write_text(json.dumps(recording, ensure_ascii=False, indent=2), encoding="utf-8")


def get_llm(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings)
    return ReplayProvider(settings.llm_replay_dir)

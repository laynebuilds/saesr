"""Claude calls for the relay steps: structured output, timeout, two retries with backoff, one
extra try if the output fails schema validation. Every call returns latency and token counts so
the trace can quote real numbers."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .config import CONFIG

log = logging.getLogger("saesr.relay.llm")
T = TypeVar("T", bound=BaseModel)

_client: Optional[anthropic.AsyncAnthropic] = None


def client() -> Optional[anthropic.AsyncAnthropic]:
    global _client
    if _client is None and CONFIG.anthropic_key:
        _client = anthropic.AsyncAnthropic(api_key=CONFIG.anthropic_key, max_retries=0)
    return _client


@dataclass
class Meta:
    model: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0

    def usage(self) -> dict:
        return {"model": self.model, "latency_ms": self.latency_ms, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class LLMUnavailable(Exception):
    pass


class SchemaError(Exception):
    pass


async def structured(model: str, system: str, content: list, schema: Type[T], timeout_s: float, max_tokens: int = 2000) -> tuple[T, Meta]:
    c = client()
    if c is None:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
    started = time.monotonic()
    schema_retry_left = 1
    last: Exception = LLMUnavailable("no attempt")
    for attempt in range(3):  # first try + two retries
        try:
            kwargs = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": content}], output_format=schema)
            if model.startswith("claude-haiku-4-5"):
                resp = await asyncio.wait_for(c.messages.parse(**kwargs), timeout_s)
            else:
                # Sonnet 5.5: server-side fallback re-runs a declined request on Anthropic's recommended model.
                resp = await asyncio.wait_for(
                    c.beta.messages.parse(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs), timeout_s
                )
            if resp.stop_reason == "refusal":
                raise LLMUnavailable("the model declined this request")
            parsed = resp.parsed_output
            if parsed is None:
                raise SchemaError(f"{schema.__name__}: output did not match the schema")
            meta = Meta(resp.model, int((time.monotonic() - started) * 1000), resp.usage.input_tokens, resp.usage.output_tokens)
            log.info("%s %s %d ms in=%d out=%d", schema.__name__, meta.model, meta.latency_ms, meta.input_tokens, meta.output_tokens)
            return parsed, meta
        except (ValidationError, SchemaError) as err:
            last = err
            if schema_retry_left == 0:
                break
            schema_retry_left -= 1
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError, LLMUnavailable) as err:
            last = err
            break  # not retryable
        except (asyncio.TimeoutError, anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError, anthropic.APIStatusError) as err:
            last = err
        if attempt < 2:
            await asyncio.sleep(0.6 * (attempt + 1))
    raise LLMUnavailable(f"{type(last).__name__}: {str(last)[:200]}")

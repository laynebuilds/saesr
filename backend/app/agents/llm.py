"""Optional LLM reasoning for the supervisor. Used only when OPENAI_API_KEY is set and the
openai package is installed (uv sync --extra agents); otherwise every caller gets None and
falls back to deterministic text, so the demo never depends on a key or the network.

The LLM never decides whether to escalate (the rules do, so behaviour is predictable). It writes
what a floor manager reads: the escalation's recommended action and the shift brief.
"""
import asyncio
import json
import logging
import os
from typing import Optional

log = logging.getLogger("saesr.agents.llm")

SYSTEM = (
    "You are the safety supervisor agent for a warehouse. Camera agents report incidents from "
    "computer vision (unsafe lifts, missing PPE, people in restricted zones). You write short, "
    "calm, specific messages for the floor manager: what is happening, where, and the single most "
    "useful action to take now. Plain text, no markdown, no emoji, no dashes as punctuation. "
    "Never invent incidents or numbers that are not in the input."
)


class Reasoner:
    def __init__(self, model: str, timeout_s: float) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self._client = None
        self.disabled_reason: Optional[str] = None
        if not os.environ.get("OPENAI_API_KEY"):
            self.disabled_reason = "OPENAI_API_KEY not set"
            return
        try:
            from openai import AsyncOpenAI  # optional dependency
        except ImportError:
            self.disabled_reason = "openai package not installed (uv sync --extra agents)"
            return
        self._client = AsyncOpenAI()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def _ask(self, prompt: str, max_tokens: int) -> Optional[str]:
        if not self._client:
            return None
        try:
            res = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.3,
                ),
                self.timeout_s,
            )
            text = (res.choices[0].message.content or "").strip()
            # house style: no em or en dashes
            return text.replace("\u2014", ", ").replace("\u2013", "-") or None
        except Exception as err:  # never let the LLM break the supervisor
            log.warning("llm call failed, using rules text: %s", err)
            return None

    async def escalation(self, finding: dict, recent: list[dict]) -> Optional[str]:
        prompt = (
            "A rule fired and the floor manager is being paged. In at most 2 sentences, say what is "
            "happening and the one action they should take now.\n"
            f"Finding: {json.dumps(finding)}\nRecent incidents (newest last): {json.dumps(recent[-8:])}"
        )
        return await self._ask(prompt, 120)

    async def brief(self, snapshot: dict, recent_findings: list[dict]) -> Optional[str]:
        prompt = (
            "Write the rolling shift brief in at most 4 short sentences: overall state, the worst "
            "area, what is still unacknowledged, and one recommendation for the rest of the shift.\n"
            f"Shift numbers: {json.dumps(snapshot)}\nFindings since last brief: {json.dumps(recent_findings)}"
        )
        return await self._ask(prompt, 180)

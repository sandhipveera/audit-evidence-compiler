"""Transport abstraction — one interface, multiple LLM backends."""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class CompletionResult:
    text: str
    model: str
    transport_name: str


class Transport(abc.ABC):
    name: str = ""

    @abc.abstractmethod
    async def available(self) -> bool:
        """Return True if this transport can be used right now."""

    @abc.abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        """Send a single completion request and return the raw text."""

    async def probe(self, model: str = "") -> tuple[bool, str]:
        """Actively verify the transport can complete a request *right now*.

        Heavier than :meth:`available` on purpose: ``available()`` only checks
        that a binary or API key is present, which misses expired CLI logins and
        rotated tokens (a vendor then silently drops out of the panel mid-run).
        ``probe()`` does a real minimal round-trip and returns ``(ok, detail)``.
        Pass ``model`` to test the exact model the panel will use (an empty string
        lets the transport pick its own default). The detail is the live model on
        success, or the truncated error on failure.
        """
        try:
            if not await self.available():
                return False, "not available (binary or API key missing)"
        except Exception as exc:  # noqa: BLE001 — surface any probe failure as not-ok
            return False, f"availability check errored: {exc}"
        try:
            res = await self.complete(
                system_prompt="Reply with the single token: OK",
                user_prompt="OK",
                model=model,
                temperature=0.0,
            )
            return True, f"ok ({res.model})"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)[:300]

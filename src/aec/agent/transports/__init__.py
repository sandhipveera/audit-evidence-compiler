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

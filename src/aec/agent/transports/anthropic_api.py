"""Transport: Anthropic Messages API (API key, ~$0.003–0.015/1K tokens)."""
from __future__ import annotations

import os

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicAPITransport(Transport):
    name = "anthropic-api"

    async def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        import anthropic

        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=model or DEFAULT_MODEL,
            max_tokens=4096,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text
        return CompletionResult(
            text=text,
            model=model or DEFAULT_MODEL,
            transport_name=self.name,
        )

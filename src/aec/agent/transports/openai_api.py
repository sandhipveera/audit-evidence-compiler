"""Transport: OpenAI Chat Completions API (API key)."""
from __future__ import annotations

import os

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "gpt-5"


class OpenAIAPITransport(Transport):
    name = "openai-api"

    async def available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        import openai

        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model=model or DEFAULT_MODEL,
            temperature=temperature,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = response.choices[0].message.content or ""
        return CompletionResult(
            text=text,
            model=model or DEFAULT_MODEL,
            transport_name=self.name,
        )

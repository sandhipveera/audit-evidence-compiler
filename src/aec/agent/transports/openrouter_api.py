"""Transport: OpenRouter API (OpenAI-compatible, any-vendor escape hatch)."""
from __future__ import annotations

import os

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "google/gemini-2.5-pro"
BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAPITransport(Transport):
    name = "openrouter-api"

    async def available(self) -> bool:
        return bool(os.environ.get("OPENROUTER_API_KEY"))

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        import openai

        client = openai.AsyncOpenAI(
            base_url=BASE_URL,
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        response = await client.chat.completions.create(
            model=model or DEFAULT_MODEL,
            temperature=temperature,
            max_tokens=4096,
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

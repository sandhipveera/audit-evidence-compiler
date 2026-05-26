"""Transport: Foundation-Sec-8B via HuggingFace Inference API (Featherless.ai backend)."""
from __future__ import annotations

import asyncio
import os

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "fdtn-ai/Foundation-Sec-8B-Instruct"


class FoundationSecAPITransport(Transport):
    name = "foundation-sec-api"

    async def available(self) -> bool:
        return bool(os.environ.get("HF_TOKEN"))

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        from huggingface_hub import InferenceClient

        client = InferenceClient(
            provider="featherless-ai",
            api_key=os.environ["HF_TOKEN"],
        )

        used_model = model or DEFAULT_MODEL
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=used_model,
                messages=messages,
                max_tokens=1024,
                temperature=temperature,
            ),
        )
        text = response.choices[0].message.content or ""
        return CompletionResult(
            text=text,
            model=used_model,
            transport_name=self.name,
        )

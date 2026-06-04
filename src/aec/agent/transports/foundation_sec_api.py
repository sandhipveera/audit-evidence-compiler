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

        def _call():
            return client.chat.completions.create(
                model=used_model,
                messages=messages,
                max_tokens=1024,
                temperature=temperature,
            )

        # The featherless backend occasionally returns a transient error or an
        # empty body under concurrent load (all four personas fire at once),
        # which would silently drop this vendor from the panel. Retry briefly so
        # the panel stays genuinely four-vendor.
        loop = asyncio.get_event_loop()
        last_exc: Exception | None = None
        text = ""
        for attempt in range(3):
            try:
                response = await loop.run_in_executor(None, _call)
                text = response.choices[0].message.content or ""
                if text.strip():
                    break
            except Exception as exc:  # noqa: BLE001 — retry any transient backend failure
                last_exc = exc
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
        else:
            if not text.strip() and last_exc is not None:
                raise last_exc

        return CompletionResult(
            text=text,
            model=used_model,
            transport_name=self.name,
        )

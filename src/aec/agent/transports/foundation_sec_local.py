"""Transport: Foundation-Sec-8B via local Ollama (OpenAI-compatible API)."""
from __future__ import annotations

import os

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "hf.co/roadus/Foundation-Sec-8B-Q4_K_M-GGUF:Q4_K_M"
DEFAULT_BASE_URL = "http://localhost:11434/v1"


class FoundationSecLocalTransport(Transport):
    name = "foundation-sec-local"

    async def available(self) -> bool:
        import httpx

        base_url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL)
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        import httpx

        base_url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL)
        used_model = model or DEFAULT_MODEL

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": used_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 1024,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        text = data["choices"][0]["message"]["content"] or ""
        return CompletionResult(
            text=text,
            model=used_model,
            transport_name=self.name,
        )

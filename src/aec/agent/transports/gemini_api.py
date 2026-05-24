"""Transport: Google Generative AI SDK (API key, free tier ~60 RPM)."""
from __future__ import annotations

import os

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "gemini-2.5-pro"


class GeminiAPITransport(Transport):
    name = "gemini-api"

    async def available(self) -> bool:
        return bool(os.environ.get("GOOGLE_API_KEY"))

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        import google.generativeai as genai

        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        gen_model = genai.GenerativeModel(
            model_name=model or DEFAULT_MODEL,
            system_instruction=system_prompt,
        )
        response = await gen_model.generate_content_async(
            user_prompt,
            generation_config={"temperature": temperature, "max_output_tokens": 4096},
        )
        return CompletionResult(
            text=response.text,
            model=model or DEFAULT_MODEL,
            transport_name=self.name,
        )

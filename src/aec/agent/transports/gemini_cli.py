"""Transport: Gemini CLI (Google OAuth, $0 per call)."""
from __future__ import annotations

import asyncio
import json
import shutil

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "gemini-2.5-pro"


class GeminiCLITransport(Transport):
    name = "gemini-cli"

    async def available(self) -> bool:
        return shutil.which("gemini") is not None

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        proc = await asyncio.create_subprocess_exec(
            "gemini",
            "--prompt", "-",
            "--output", "json",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(
                f"gemini CLI exited {proc.returncode}: {stderr.decode('utf-8', errors='replace')}"
            )
        output = stdout.decode("utf-8").strip()
        try:
            parsed = json.loads(output)
            text = parsed.get("response", parsed.get("result", output))
        except json.JSONDecodeError:
            text = output
        return CompletionResult(
            text=text,
            model=model or DEFAULT_MODEL,
            transport_name=self.name,
        )

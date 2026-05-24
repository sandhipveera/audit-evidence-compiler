"""Transport: Claude Code CLI (OAuth, $0 per call)."""
from __future__ import annotations

import asyncio
import json
import shutil

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicCLITransport(Transport):
    name = "anthropic-cli"

    async def available(self) -> bool:
        return shutil.which("claude") is not None

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        requested_model = model or DEFAULT_MODEL
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "--print",
            "--output-format", "json",
            "--model", requested_model,
            "--system-prompt", system_prompt,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(user_prompt.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode}: {stderr.decode('utf-8', errors='replace')}"
            )
        output = stdout.decode("utf-8").strip()
        try:
            parsed = json.loads(output)
            if parsed.get("is_error"):
                raise RuntimeError(
                    f"Claude CLI runtime error (exit 0 but is_error=true): "
                    f"{parsed.get('result', 'unknown')}"
                )
            result_text = parsed.get("result", "")
            if "Not logged in" in result_text or "Please run /login" in result_text:
                raise RuntimeError(
                    f"Claude OAuth expired or missing. Run: claude /login. "
                    f"CLI said: {result_text}"
                )
            text = parsed.get("result", output)
        except json.JSONDecodeError:
            text = output
        return CompletionResult(
            text=text,
            model=requested_model,
            transport_name=self.name,
        )

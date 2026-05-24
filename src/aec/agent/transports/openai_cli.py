"""Transport: Codex CLI (ChatGPT OAuth, $0 per call)."""
from __future__ import annotations

import asyncio
import json
import shutil

from aec.agent.transports import CompletionResult, Transport

DEFAULT_MODEL = "gpt-5.5"


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _extract_text_from_event(event: object) -> str:
    if not isinstance(event, dict):
        return ""

    for key in ("output", "result", "message", "text"):
        value = event.get(key)
        if isinstance(value, str):
            return value

    content = _content_to_text(event.get("content"))
    if content:
        return content

    item = event.get("item")
    if isinstance(item, dict):
        for key in ("output", "result", "message", "text"):
            value = item.get(key)
            if isinstance(value, str):
                return value

        content = _content_to_text(item.get("content"))
        if content:
            return content

    return ""


def _extract_codex_text(output: str) -> str:
    """Extract the final assistant message from Codex JSON or JSONL output."""
    text = output.strip()
    if not text:
        return text

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    else:
        extracted = _extract_text_from_event(parsed)
        if extracted:
            return extracted

    messages: list[str] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        extracted = _extract_text_from_event(event)
        if extracted:
            messages.append(extracted)

    return messages[-1] if messages else text


class OpenAICLITransport(Transport):
    name = "openai-cli"

    async def available(self) -> bool:
        return shutil.which("codex") is not None

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> CompletionResult:
        prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        requested_model = model or DEFAULT_MODEL
        proc = await asyncio.create_subprocess_exec(
            "codex", "exec", "--json", "--model", requested_model, "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(
                f"codex CLI exited {proc.returncode}: {stderr.decode('utf-8', errors='replace')}"
            )
        raw_output = stdout.decode("utf-8")
        for line in raw_output.splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") in ("error", "turn.failed"):
                raise RuntimeError(
                    f"Codex CLI error: {evt.get('message') or evt.get('error', {}).get('message')}"
                )
        return CompletionResult(
            text=_extract_codex_text(raw_output),
            model=requested_model,
            transport_name=self.name,
        )

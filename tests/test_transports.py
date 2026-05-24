"""Transport-level parsing tests."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from aec.agent.transports.anthropic_cli import AnthropicCLITransport
from aec.agent.transports.openai_cli import OpenAICLITransport, _extract_codex_text


def test_extract_codex_text_from_single_json_object():
    output = json.dumps({"output": '{"verdict":"PASS"}'})

    assert _extract_codex_text(output) == '{"verdict":"PASS"}'


def test_extract_codex_text_from_jsonl_message_event():
    output = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"verdict":"FAIL"}',
                            }
                        ],
                    },
                }
            ),
            json.dumps({"type": "turn.completed"}),
        ]
    )

    assert _extract_codex_text(output) == '{"verdict":"FAIL"}'


def _mock_proc(stdout: bytes, stderr: bytes = b"", returncode: int = 0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


@pytest.mark.asyncio
async def test_anthropic_cli_raises_on_is_error():
    payload = json.dumps({"is_error": True, "result": "Not logged in"})
    proc = _mock_proc(stdout=payload.encode())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        transport = AnthropicCLITransport()
        with pytest.raises(RuntimeError, match="is_error=true"):
            await transport.complete("system", "user", "claude-sonnet-4-6", 0.3)


@pytest.mark.asyncio
async def test_anthropic_cli_raises_on_auth_failure_string():
    payload = json.dumps({"is_error": False, "result": "Please run /login to authenticate"})
    proc = _mock_proc(stdout=payload.encode())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        transport = AnthropicCLITransport()
        with pytest.raises(RuntimeError, match="OAuth expired"):
            await transport.complete("system", "user", "claude-sonnet-4-6", 0.3)


@pytest.mark.asyncio
async def test_openai_cli_raises_on_turn_failed():
    stream = "\n".join([
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "error", "message": "invalid model: gpt-5"}),
        json.dumps({"type": "turn.failed", "error": {"message": "model not supported"}}),
    ])
    proc = _mock_proc(stdout=stream.encode())

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        transport = OpenAICLITransport()
        with pytest.raises(RuntimeError, match="Codex CLI error"):
            await transport.complete("system", "user", "gpt-5.5", 0.3)

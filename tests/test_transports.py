"""Transport-level parsing tests."""
from __future__ import annotations

import json

from aec.agent.transports.openai_cli import _extract_codex_text


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

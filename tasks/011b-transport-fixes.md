# Task 011b — Transport bug fixes (Claude is_error + Codex model name)

**Goal:** Two surgical fixes so the panel runs at full 3-of-3 strength instead of degrading to 1-of-3 every demo. ~30 LOC total.

## Bug 1 — Claude CLI returns is_error=true silently

The Claude CLI exits 0 even when auth fails or rate limits hit. The error surfaces only in the JSON body as `is_error: true` + a human-readable `result` field. The current transport only checks `proc.returncode != 0`, so it treats "Not logged in" as a successful response and the panel sees garbage.

**Fix in `src/aec/agent/transports/anthropic_cli.py`:**

After the existing `json.loads(output)` line, add:

```python
if parsed.get("is_error"):
    raise RuntimeError(
        f"Claude CLI runtime error (exit 0 but is_error=true): "
        f"{parsed.get('result', 'unknown')}"
    )
```

Also short-circuit on common auth failure strings even if `is_error` is somehow false:
```python
result_text = parsed.get("result", "")
if "Not logged in" in result_text or "Please run /login" in result_text:
    raise RuntimeError(
        f"Claude OAuth expired or missing. Run: claude /login. "
        f"CLI said: {result_text}"
    )
```

## Bug 2 — Codex CLI rejects `gpt-5` on ChatGPT accounts

ChatGPT Plus/Team subscriptions don't expose `gpt-5` via Codex CLI. Account default is `gpt-5.5`. The Engineer persona currently requests `gpt-5` → 400 invalid_request_error.

**Fix in `src/aec/agent/personas/engineer.md` frontmatter:**

Change:
```yaml
transports:
  - openai-cli:
      model: gpt-5
```

To:
```yaml
transports:
  - openai-cli:
      model: gpt-5.5
```

Same fix in any other persona referencing `gpt-5` for the `openai-cli` transport. (API transports may still use `gpt-5` if there's a separate OpenAI API key with that access — keep the CLI/API model names independent.)

**Also improve `src/aec/agent/transports/openai_cli.py` error handling:**

The codex CLI emits JSON-per-line. On model-not-supported the stream contains `{"type":"error", ...}` and `{"type":"turn.failed", ...}` but exit code may still be 0 in some versions. Parse the stream defensively:

```python
# After collecting stdout lines, check for turn.failed:
for line in stdout.decode("utf-8").splitlines():
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        continue
    if evt.get("type") in ("error", "turn.failed"):
        raise RuntimeError(
            f"Codex CLI error: {evt.get('message') or evt.get('error', {}).get('message')}"
        )
```

## Tests

Update `tests/test_transports.py`:

- `test_anthropic_cli_raises_on_is_error` — mock subprocess that returns `{"is_error": true, "result": "Not logged in"}`, assert RuntimeError with "OAuth expired" in message.
- `test_openai_cli_raises_on_turn_failed` — mock subprocess that returns the codex error stream, assert RuntimeError with "Codex CLI error" in message.

Both use `unittest.mock.patch("asyncio.create_subprocess_exec", ...)` — no real subprocess invocation.

## Verification (manual, post-merge)

```bash
# Should now run all 3 personas successfully
aec_demo --sample soc2-cc61

# Look for in the output:
# - CONSENSUS: <something>  (not "FAILED: All transports exhausted")
# - No persona panel shows "FAILED" in the TUI
# - All 3 personas display verdicts
```

## Definition of done

- Both transport files updated with defensive error parsing
- Engineer persona uses `gpt-5.5` for openai-cli
- 2 new tests pass
- Manual `aec_demo` run shows all 3 personas with verdicts (assuming Claude OAuth is fresh)

## Out of scope

- Don't add OpenRouter fallback to personas (separate decision)
- Don't refactor the transport class hierarchy
- Don't switch Codex to API mode (we want the OAuth/subscription path)

# Task 002 — Splunk MCP client

**Goal:** Get a working `splunk_client.py` that executes a SPL string and returns rows, via Splunk MCP Server.

## Acceptance

- `python -m aec.mcp.splunk_client --spl 'index=_internal | head 5'` returns 5 rows as JSON.
- Works against local Splunk Docker container (per `.env.example`).
- Handles auth errors + bad-SPL errors with usable messages (not stacktraces).

## Approach

1. Stand up Splunk locally:
   ```bash
   docker run -d --name splunk -p 8000:8000 -p 8089:8089 \
     -e SPLUNK_START_ARGS=--accept-license \
     -e SPLUNK_PASSWORD=changeme-on-first-run \
     splunk/splunk:latest
   ```
2. Pick MCP server. Try in this order:
   - [splunk/mcp-server-for-splunk](https://github.com/splunk/mcp-server-for-splunk) — official-adjacent
   - [livehybrid/splunk-mcp](https://github.com/livehybrid/splunk-mcp) — community fallback
3. Write `src/aec/mcp/splunk_client.py` using the Python `mcp` SDK to connect (stdio first, HTTP if it works).
4. Expose two methods minimum: `execute_spl(query: str, earliest: str = "-24h") -> list[dict]` and `list_indexes() -> list[str]`.

## Out of scope

- Streaming / long-running searches (use `oneshot` SPL only for MVP).
- Saved-search execution (do that in v2).
- Caching layer.

## Files to create

- `src/aec/mcp/__init__.py`
- `src/aec/mcp/splunk_client.py`
- `infra/docker-compose.yml` (Splunk + optionally MCP server as a service)
- `tests/test_splunk_client.py` (one integration test, marked `@pytest.mark.integration` so it skips when SPLUNK_HOST unreachable)

## Notes

- If both MCP servers prove brittle within first 2 hours, fall back to using `splunk-sdk` directly and document the MCP integration as v2. Don't burn the whole session on MCP wiring.
- Record any MCP server quirks discovered in `docs/decisions/001-mcp-server-choice.md`.

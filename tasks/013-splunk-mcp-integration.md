# Task 013 — Splunk MCP integration (THE hackathon hook)

**Goal:** Replace the bare REST client with a **Splunk MCP Server** integration. This is THE "Agentic Ops" angle the hackathon brief explicitly calls out. The panel and demo logic stay the same; the SPL execution layer changes underneath.

**Critical demand:** support *both* MCP server implementations behind a runtime switch:
- `splunk/mcp-server-for-splunk` (official-adjacent — first preference)
- `livehybrid/splunk-mcp` (mature community fallback)

Choose at runtime via `AEC_SPLUNK_MCP_SERVER=splunk-official | livehybrid` env var. Default: `splunk-official`. Justification: maximum compatibility for judges who may have either installed.

**Budget:** ~600–800 LOC + integration runbook. ~3 days of YC time.

## Why this is the most important task

The hackathon brief explicitly lists *"Splunk MCP Server"* as one of the headline capabilities to lean on. Most other entries will either:
- Skip MCP entirely and use the REST API (boring)
- Use a single MCP implementation (vendor lock-in)
- Build their own (over-engineered)

Using *both* official-adjacent and community MCP servers behind a single runtime switch demonstrates platform fluency and de-risks the demo. **If MCP server A breaks during the video, MCP server B runs the same demo.**

## What to build

### 1. MCP client abstraction layer

`src/aec/splunk/mcp/__init__.py` exposes:
```python
async def execute_spl(query: str, time_window: str = "-30d") -> dict
async def list_indexes() -> list[str]
async def get_sourcetypes(index: str) -> list[str]
async def get_metadata() -> dict   # Splunk version, license type, etc.
async def probe() -> dict          # health check
```

These are the agent's tools through MCP. The function signatures match the REST client's so consumers don't care which is backing.

### 2. Two transport implementations

`src/aec/splunk/mcp/transports/splunk_official.py` (~250 LOC):
- Spawn / connect to [`splunk/mcp-server-for-splunk`](https://github.com/splunk/mcp-server-for-splunk)
- Use the Python `mcp` SDK to communicate (stdio or HTTP, both)
- Map our 5 functions onto whatever the server exposes
- Handle the server's tool schema differences gracefully

`src/aec/splunk/mcp/transports/livehybrid.py` (~250 LOC):
- Same shape, against [`livehybrid/splunk-mcp`](https://github.com/livehybrid/splunk-mcp)
- Document the API surface differences in a comment block at the top
- Same return-value normalization

### 3. Runtime router

`src/aec/splunk/mcp/router.py` (~100 LOC):
- Reads `AEC_SPLUNK_MCP_SERVER` env var
- Imports the matching transport
- On startup, probes both servers (if both reachable) and logs which is active
- Falls back to the *other* server if the configured one is unreachable
- Records the active server in every `EvidenceSnapshot.metadata.mcp_server`

### 4. CLI flag

`cli/aec_demo.py` gains `--mcp <official|livehybrid|rest>`:
- `official` → forces splunk-official
- `livehybrid` → forces livehybrid
- `rest` → bypasses MCP entirely, uses task 012's REST client
- Default: read `AEC_SPLUNK_MCP_SERVER` env, fallback to `official`

### 5. Docker compose for both servers

`infra/docker-compose.mcp.yml`:
```yaml
services:
  splunk:
    image: splunk/splunk:latest
    # ... (from task 012)
  mcp-official:
    image: ghcr.io/splunk/mcp-server-for-splunk:latest  # or build from source
    ports: ["8765:8765"]
    environment:
      SPLUNK_HOST: splunk
      SPLUNK_TOKEN: ${SPLUNK_TOKEN}
  mcp-livehybrid:
    build: ./infra/livehybrid-mcp
    ports: ["8766:8766"]
    environment:
      SPLUNK_HOST: splunk
      SPLUNK_TOKEN: ${SPLUNK_TOKEN}
```

`docker compose -f infra/docker-compose.mcp.yml up -d` brings up all three (Splunk + both MCP servers).

### 6. Add MCP to the EvidenceSnapshot provenance

Every `EvidenceSnapshot` gains a field `mcp_server: str | null` recording which MCP transport executed the SPL. This shows up in:
- `audit_trail.jsonl`
- The xlsx Manifest sheet
- The transcript markdown

That's the credibility line for an auditor: *"This evidence was retrieved via splunk-official MCP server v0.3.2 against Splunk Enterprise 9.2.1 at <UTC ts>."*

## Tests

- `tests/test_mcp_router.py` (~80 LOC) — runtime switch logic, fallback, env var precedence
- `tests/test_mcp_official.py` (~80 LOC) — mock the official server's tool schema, verify SPL → result roundtrip
- `tests/test_mcp_livehybrid.py` (~80 LOC) — same for livehybrid
- `tests/test_mcp_integration.py` (~50 LOC) — `@pytest.mark.integration`, gated on actual MCP servers running locally

## Documentation

- `docs/mcp-setup.md` — how to bring up both servers, troubleshooting, transport-difference matrix
- README updated: replace any "REST client" framing with "Splunk MCP Server (both official-adjacent and community implementations)"
- Architecture diagram: split the single MCP node into two parallel boxes with the router in front

## Constraints

- New dependency: Python `mcp` SDK (https://github.com/modelcontextprotocol/python-sdk). Already in pyproject.toml (per task 010); confirm version compatibility with both servers.
- Both server installs are external (docker-compose); not bundled into our repo.
- If `splunk-official` doesn't exist as a public docker image at submission time, fallback: build it from source in `infra/Dockerfile.mcp-official` and document.
- LOC budget: 800. Watch for transport abstraction over-engineering.

## Definition of done

- `aec_demo --mcp official --control CC6.1` works end-to-end
- `aec_demo --mcp livehybrid --control CC6.1` works end-to-end
- `aec_demo --mcp rest --control CC6.1` still works (task 012's path)
- `docker compose -f infra/docker-compose.mcp.yml up -d` brings up Splunk + both MCP servers
- Every `EvidenceSnapshot` records the active MCP server in provenance
- All test files pass
- README leads with MCP integration, not REST
- Architecture diagram reflects the dual-MCP reality

## Demo cue (this is what judges will pause and re-watch)

The video shows the SPL hitting MCP, with a brief callout:

```
$ aec_demo --control CC6.1 --mcp official
[1/5] MCP server: splunk-official (v0.3.2 @ localhost:8765)
      Fallback configured: livehybrid (v1.4.0 @ localhost:8766)
[2/5] Generating SPL for SOC 2 CC6.1.c...
[3/5] Executing via MCP tool call (execute_spl)...
      [shows the JSON-RPC tool invocation on screen for 2s]
[4/5] Panel debate...
[5/5] Verdict: FAIL.

provenance: mcp_server=splunk-official-0.3.2, splunk=9.2.1
```

Then a 2-second beat: `aec_demo --mcp livehybrid` and the *same demo* runs against the community server. That redundancy is the "I built this for the platform, not for one vendor's flavor" signal.

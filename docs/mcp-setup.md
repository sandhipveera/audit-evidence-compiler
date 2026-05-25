# Splunk MCP Server Setup

This runbook covers configuring both Splunk MCP server implementations for use with the Audit Evidence Compiler.

## Overview

AEC supports two MCP server implementations behind a runtime switch:

| Server | Repo | Default Port | Env Var |
|--------|------|-------------|---------|
| **splunk-official** | [splunk/mcp-server-for-splunk](https://github.com/splunk/mcp-server-for-splunk) | 8765 | `AEC_MCP_OFFICIAL_URL` |
| **livehybrid** | [livehybrid/splunk-mcp](https://github.com/livehybrid/splunk-mcp) | 8766 | `AEC_MCP_LIVEHYBRID_URL` |

Both connect to the same Splunk Enterprise instance. AEC auto-detects which is available and falls back to the other if the primary is unreachable.

## Quick Start (Docker Compose)

```bash
# Set your Splunk token
export SPLUNK_TOKEN=your-token

# Bring up Splunk + both MCP servers
docker compose -f infra/docker-compose.mcp.yml up -d

# Run with the official MCP server
aec_demo --control CC6.1 --mcp official

# Run with the community MCP server
aec_demo --control CC6.1 --mcp livehybrid

# Run with direct REST API (no MCP)
aec_demo --control CC6.1 --mcp rest
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AEC_SPLUNK_MCP_SERVER` | `splunk-official` | Which MCP server to use: `splunk-official` or `livehybrid` |
| `AEC_MCP_OFFICIAL_URL` | `http://localhost:8765` | URL for the splunk-official MCP server |
| `AEC_MCP_LIVEHYBRID_URL` | `http://localhost:8766` | URL for the livehybrid MCP server |
| `AEC_MCP_OFFICIAL_CMD` | (none) | Stdio command to launch splunk-official (alternative to HTTP) |
| `AEC_MCP_LIVEHYBRID_CMD` | (none) | Stdio command to launch livehybrid (alternative to HTTP) |

## Transport Differences

The two MCP servers expose slightly different tool schemas. AEC normalizes both.

| AEC Function | splunk-official tool | livehybrid tool | Notes |
|-------------|---------------------|-----------------|-------|
| `execute_spl()` | `run_search(query, earliest_time, latest_time, max_count)` | `search_splunk(search_query, earliest_time, latest_time)` | Different param names (`query` vs `search_query`) |
| `list_indexes()` | `list_indexes()` | `list_indexes(app)` | livehybrid takes optional `app` context |
| `get_sourcetypes()` | `get_index_info(index_name)` | `list_sourcetypes_for_index(index)` | Different names and param names |
| `get_metadata()` | `get_server_info()` | `get_server_info()` | Same name, different response shape |

## Fallback Behavior

1. AEC reads `AEC_SPLUNK_MCP_SERVER` (or `--mcp` CLI flag)
2. Connects to the preferred server
3. If the preferred server is unreachable, automatically tries the other
4. If both are unreachable, raises an error
5. On startup, probes both servers and logs which is active + which is available as fallback

## Stdio vs HTTP

Both transports support two connection modes:

- **HTTP** (default): Connect to a running MCP server via Streamable HTTP. Set the URL env var.
- **Stdio**: Launch the MCP server as a subprocess. Set the CMD env var (e.g., `AEC_MCP_OFFICIAL_CMD="python -m splunk_mcp_server"`).

HTTP is recommended for Docker Compose deployments. Stdio is useful for local development.

## Provenance

Every `EvidenceSnapshot` records which MCP transport executed the SPL:

```json
{
  "control_id": "CC6.1",
  "mcp_server": "splunk-official-0.3.2",
  "spl_executed": "index=botsv3 sourcetype=o365:management:activity ...",
  "timestamp": "2026-05-25T03:00:00Z"
}
```

This appears in:
- `audit_trail.jsonl` — every snapshot's `mcp_server` field
- CLI output — `provenance: mcp_server=splunk-official-0.3.2`

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `MCPTransportError: Failed to connect` | Server not running. Check `docker ps` for the container. |
| `MCPTransportError: no tool for 'execute_spl'` | Server version mismatch. AEC tries known tool name aliases; if none match, the server's tool schema has changed. |
| `Both MCP servers unreachable` | Neither server is running. Start at least one with `docker compose up`. |
| Fallback used unexpectedly | Check the preferred server's health: `curl http://localhost:8765/mcp` |

## Building MCP Servers from Source

If the Docker images aren't available on ghcr.io, build from source:

```bash
# splunk-official
docker build -t aec-mcp-official -f infra/Dockerfile.mcp-official infra/

# livehybrid
docker build -t aec-mcp-livehybrid -f infra/Dockerfile.mcp-livehybrid infra/
```

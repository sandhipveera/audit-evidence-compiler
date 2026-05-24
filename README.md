# Audit Evidence Auto-Compiler

> **Splunk Agentic Ops Hackathon 2026 — Security Track**
> An AI agent that converts compliance questions into Splunk queries and produces audit-ready evidence artifacts (xlsx) for SOC 2, ISO 27001, and NIST CSF.

```
$ aec ask --framework soc2 --control CC6.1 --output gap_report.xlsx

[1/4] Mapping SOC 2 CC6.1 → 3 internal controls (Access Control, MFA, Privilege Review)
[2/4] Generated 3 SPL queries (showing #1: index=* action=login_failed | stats count by user)
[3/4] Executing against Splunk via MCP... 2/3 returned evidence, 1 gap
[4/4] Wrote gap_report.xlsx (1 finding, severity High)
```

## Why this exists

vCISO consultants spend 40+ hours per SOC 2 audit cycle hand-pulling evidence from Splunk and reformatting it into auditor-acceptable artifacts. This agent does it in one prompt.

## What it does

1. **Control mapping layer** — translates `"SOC 2 CC6.1"` or `"NIST CSF PR.AC-1"` into the specific internal controls and evidence types required, using a curated prior built from 89 production vCISO templates.
2. **SPL generation layer** — uses [Splunk MCP Server](https://github.com/splunk/mcp-server-for-splunk) as the agent's tool layer. The LLM generates SPL, the MCP server executes it.
3. **Evidence formatter** — drops results into the same Audit Findings Remediation Tracker xlsx format that real audit committees already use.
4. **Remediation linker** — for gaps, suggests concrete fixes (configuration changes, missing log sources, policy updates).

## Architecture

![Architecture](architecture.svg)

See [ARCHITECTURE.md](ARCHITECTURE.md) for component detail.

## Quick start

```bash
# 1. Install
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY and SPLUNK_* credentials

# 3. Start Splunk + MCP server (Docker compose provided)
docker compose -f infra/docker-compose.yml up -d

# 4. Run
aec ask --framework soc2 --control CC6.1 --output gap_report.xlsx
```

## Open-source artifact: vCISO Control Mapping Library

Even if you don't use the agent, the [`src/aec/priors/catalog.json`](src/aec/priors/catalog.json) file is a standalone library mapping ~36 internal cybersecurity controls across **ISO 27001, NIST 800-53, NIST CSF, SOC 2, and COBIT**, each tagged with the Splunk evidence patterns required to prove compliance. Derived from real consulting engagements; sanitized for open distribution.

## Demo

3-minute video: [link forthcoming]

## License

Apache-2.0. See [LICENSE](LICENSE).

## Author

[Veera Sandiparthi](mailto:reachveera2024@gmail.com), AccessQuint LLC — vCISO consultancy, Pleasanton CA.

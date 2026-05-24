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
2. **SPL generation layer** — LLM generates SPL targeted at the control's evidence question.
3. **SPL validator** — blocks unbounded searches, unknown indexes, and destructive commands (`| delete`, `| outputlookup`) *before* anything hits Splunk. Rejection becomes a gap finding with a clear reason.
4. **Splunk executor** — runs validated SPL via the [Splunk MCP Server](https://github.com/splunk/mcp-server-for-splunk).
5. **Evidence normalizer** — wraps every result with full provenance (control, SPL, sourcetypes, timestamp, model metadata) into `audit_trail.jsonl`.
6. **Evidence formatter** — drops results into the same Audit Findings Remediation Tracker xlsx format that real audit committees already use; gap findings get severity, root cause, and LLM-drafted remediation.
7. **Review gate (optional)** — LangGraph interrupt for human approve/edit/reject before the xlsx is written; off by default for demo, on for enterprise use.

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

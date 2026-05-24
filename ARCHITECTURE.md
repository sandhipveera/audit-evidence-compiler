# Architecture

## Pipeline (6 nodes + 1 guard, 1 graph)

See [`architecture.mmd`](architecture.mmd) for the rendered diagram.

```
operator prompt
      │
      ▼
1. Control Mapper      ← catalog.json (Control Evidence Catalog: SOC2/NIST/ISO)
      │
      ▼
2. SPL Generator       ← spl_hints (per control category)
      │
      ▼
3. SPL Validator       ← policy.json (allowed indexes, forbidden cmds, time bounds)
      │                  on reject → straight to formatter as gap finding
      ▼
4. Splunk Executor (via Splunk MCP Server → Splunk Enterprise / BOTS v3)
      │
      ▼
5. Evidence Normalizer → writes EvidenceSnapshot to audit_trail.jsonl
      │
      ▼
6. Evidence Formatter  → passed-evidence row OR gap finding (severity-scored)
      │
      ▼
[Review Gate]          → auto (skip) or interactive (LangGraph interrupt)
      │
      ▼
gap_report.xlsx  +  audit_package.md  +  audit_trail.jsonl
```

## Why these guards exist

- **SPL Validator** — the LLM can hallucinate indexes that don't exist, omit time bounds (unbounded searches DoS the Splunk trial), or emit destructive commands (`| delete`, `| outputlookup`). The validator enforces an execution policy *before* any SPL hits the wire. Rejection routes to a gap finding with a clear reason. This is what makes the agent audit-defensible.
- **Evidence Normalizer** — captures full provenance (control_id, exact SPL run, sourcetypes touched, row count, timestamp, LLM model + prompt id) into `audit_trail.jsonl`. Without this the agent's output isn't credible to an auditor.
- **Review Gate** — LangGraph `interrupt()` pauses for human approve/edit/reject. Off by default for demo speed; flip on with `--review=interactive` for the enterprise mode.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Agent runtime | Python 3.11 + LangGraph | Splunk ecosystem is Python-first; LangGraph's stateful nodes map 1:1 to the pipeline and make HITL approval gates trivial |
| LLM | Claude Sonnet 4 (Anthropic SDK) | Highest reasoning quality for SPL synthesis + audit narrative |
| Splunk integration | [Splunk MCP Server](https://github.com/splunk/mcp-server-for-splunk) | The "Agentic Ops" hook — agent uses MCP tools to execute search, list indexes, get sourcetypes |
| Splunk runtime | `splunk/splunk` Docker + BOTS v3 sample data | Reproducible by judges with one `docker compose up` |
| Output | openpyxl → real vCISO audit template | Auditors recognize the format instantly |
| Priors | JSON, hand-curated from 89 vCISO templates | The "I lived this" unfair advantage |

## Priors layer

`src/aec/priors/catalog.json` is built once from `~/Documents/AI Projects/accessquint/core-biz/` (local-only path, NOT committed) by running:

```bash
python -m aec.priors.build_from_xlsx \
  --source "/Users/admin/Documents/AI Projects/accessquint/core-biz" \
  --out src/aec/priors/catalog.json
```

The output JSON ships in the repo as the open-source vCISO Control Mapping Library — useful on its own without the agent.

## What's NOT in this repo

- Raw vCISO core-biz xlsx files (client-derived IP)
- Production Splunk credentials
- Any code from the AccessQuint main app

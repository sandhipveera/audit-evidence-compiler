# Architecture

## Pipeline (4 stages, 1 graph)

```
operator prompt
      │
      ▼
┌─────────────────────────┐
│ 1. Control Mapper       │  in: "SOC 2 CC6.1"
│    (LangGraph node)     │  out: [internal_controls], evidence_questions
│    prior: catalog.json  │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 2. SPL Generator        │  in: control + evidence_question + spl_hint
│    (LLM call: Claude)   │  out: SPL string + expected_columns
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 3. Splunk Executor      │  in: SPL string
│    (MCP tool call)      │  out: rows, count, error?
│    via Splunk MCP svr   │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│ 4. Evidence Formatter   │  in: control + rows
│    (openpyxl)           │  out: row appended to Audit Findings xlsx
│                          │       — passed evidence OR gap finding
└─────────────────────────┘
```

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

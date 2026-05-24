# Architecture

## The two technical differentiators

This isn't "LLM + Splunk MCP." Two design choices set it apart:

1. **Three-Agent Panel Debate — Claude × GPT × Gemini.** Every finding is judged by three personas, *each running on a different model from a different vendor*: Auditor (Claude Sonnet 4, reads the control literally), Engineer (GPT-5, reads the SPL technically), Adversary (Gemini 2.5 Pro, tries to disprove the PASS verdict). This is deliberate — disagreement between independently-trained models is meaningful signal; disagreement between same-model-different-prompts is performative. Consensus rule: lowest verdict wins. The full debate transcript ships in `audit_trail.jsonl`. Graceful fallback to single-vendor mode when keys are missing.
2. **Merkle-chained Evidence Trail** — every snapshot in `audit_trail.jsonl` includes the SHA-256 hash of the previous snapshot. The final xlsx carries the chain root in a `Manifest` sheet. `aec verify gap_report.xlsx` recomputes the chain and detects any post-hoc edit to either artifact.

Together: the agent shows its work, and the work can't be silently rewritten.

## Pipeline (8 nodes + 2 guards, 1 graph)

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
5. Evidence Normalizer → EvidenceSnapshot
      │
      ▼
6. Panel Debate        → 3 personas (Auditor / Engineer / Adversary) in parallel
      │                  Adversary may emit counter-searches → loop back to (4) once
      ▼
7. Consensus           → lowest-of-three verdict + transcript
      │
      ▼
8. Evidence Formatter  → passed-evidence row OR gap finding (severity-scored)
      │
      ▼
[Review Gate]          → auto (skip) or interactive (LangGraph interrupt)
      │
      ▼
[Merkle Chain Sealer]  → hashes every snapshot, embeds root in xlsx Manifest sheet
      │
      ▼
gap_report.xlsx  +  audit_package.md  +  audit_trail.jsonl  → all verifiable via `aec verify`
```

## Why these guards exist

- **SPL Validator** — the LLM can hallucinate indexes that don't exist, omit time bounds (unbounded searches DoS the Splunk trial), or emit destructive commands (`| delete`, `| outputlookup`). The validator enforces an execution policy *before* any SPL hits the wire. Rejection routes to a gap finding with a clear reason. This is what makes the agent audit-defensible.
- **Evidence Normalizer** — captures full provenance (control_id, exact SPL run, sourcetypes touched, row count, timestamp, LLM model + prompt id) into `audit_trail.jsonl`. Without this the agent's output isn't credible to an auditor.
- **Review Gate** — LangGraph `interrupt()` pauses for human approve/edit/reject. Off by default for demo speed; flip on with `--review=interactive` for the enterprise mode.
- **Panel Debate (task 007)** — three parallel LLM calls with distinct system prompts. The Adversary persona is the only one allowed to propose new counter-searches (one round of recurrence). Consensus is mechanical (lowest verdict wins) — no LLM "tiebreaker" in the critical path, so the result is reproducible. Personas are stored as plain-markdown system prompts in `src/aec/agent/personas/` so you can edit them without touching code.
- **Merkle Chain Sealer (task 008)** — pure SHA-256, no signing infra. Canonical JSON serialization (sorted keys, no whitespace, prev/this hash fields excluded from input). `aec verify` recomputes the chain and cross-checks the manifest root in the xlsx against the trail tip. Exit code 1 on any mismatch.

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

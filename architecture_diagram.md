# Architecture Diagram — Audit Evidence Auto-Compiler (Tessera)

One LangGraph agent pulls real Splunk data, puts it in front of four rival AI vendors, reconciles their
verdicts mechanically, and seals the result in a tamper-evident Merkle chain — then writes the verdict
back into Splunk.

![Tessera architecture — triggers, LangGraph evidence agent, four-vendor panel, mechanical consensus, Merkle seal, and Splunk write-back](web/static/slide-architecture.png)

## Pipeline (LangGraph)

```mermaid
graph TD
    subgraph "Triggers"
      T1[Operator prompt<br/>'SOC 2 CC6.1 evidence']
      T2[Splunk alert webhook<br/>incident → controls]
    end

    subgraph "LangGraph Orchestration (graph.py)"
      N1[control_mapper] --> N2[spl_generator]
      N2 --> N3[spl_validator · policy gate]
      N3 --> N4{HITL gate}
      N4 -->|approve| N5[mcp_executor]
      N5 --> N6[evidence_normalizer]
      N6 --> N7[panel_round_1]
      N7 --> N8[adversary_search_validator]
      N8 --> N9[mcp_executor — counter-SPL]
      N9 --> N10[panel_round_2]
      N10 --> N11[consensus · lowest-of-four]
      N11 --> N12{HITL gate}
      N12 -->|approve| N13[evidence_formatter]
      N13 --> N14[merkle_chain_sealer]
    end

    subgraph "Splunk (real BOTS v3 · 1.94M events)"
      S1[BOTS v3 dataset]
      S2[splunk-official MCP]
      S3[livehybrid MCP]
      S4[REST API]
      S5[(index=aec_audit<br/>HEC write-back)]
    end

    subgraph "Panel — four rival vendors"
      P1[Auditor<br/>Claude Sonnet 4]
      P2[Engineer<br/>GPT-5.5 via Codex]
      P3[Adversary<br/>Gemini 2.5 Pro]
      P4[Security Model<br/>Foundation-Sec-8B]
    end

    subgraph "Outputs"
      O1[gap_report.xlsx]
      O2[Executive Compliance Report]
      O3[audit_trail.jsonl]
      O4[aec verify · /verify portal]
    end

    T1 & T2 --> N1
    N5 --> S2 & S3 & S4
    S1 --- S2 & S3 & S4
    N7 & N10 --> P1 & P2 & P3 & P4
    N14 --> O1 & O2 & O3 & O4
    N14 --> S5
```

## Notes

- **Consensus is mechanical** — severity ordering `PASS < PARTIAL < FAIL < INSUFFICIENT`, lowest verdict
  wins, no LLM tiebreaker. Fully reproducible.
- **Splunk transport is pluggable** at runtime: `AEC_SPLUNK_MCP_SERVER=official|livehybrid|rest`.
- **Evidence is tamper-evident** — each snapshot is SHA-256 hashed over canonical JSON and chained;
  `aec verify` (or the public `/verify` portal) recomputes the chain to detect any post-collection edit.
- **Four organizations, four training sets** — Claude (Anthropic), GPT-5.5 (OpenAI), Gemini (Google),
  Foundation-Sec-8B (Cisco/Splunk, via Hugging Face / Featherless.ai) — for maximum independence.

See [`README.md`](README.md) for the full feature set and run instructions.

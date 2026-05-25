# Hackathon Portal Submission Text
## Splunk Agentic Ops Hackathon 2026 — Security Track

Copy-paste each field directly into the submission form.

---

## Project Name

Audit Evidence Auto-Compiler

---

## One-Line Description (tweet-length)

An AI agent that converts a compliance question into Splunk evidence, runs a three-vendor panel debate, and produces tamper-evident audit artifacts in under 30 seconds.

---

## Project Description (500 words)

**The problem:** A SOC 2 audit cycle costs a vCISO 40+ hours. Most of that time is spent pulling evidence from Splunk, deciding whether it actually satisfies the control, reformatting it into auditor-acceptable artifacts, and writing the rationale. This is mechanical work — and it's exactly the kind of work that should be automated.

**What it does:** The Audit Evidence Auto-Compiler is an agentic pipeline that takes a compliance question ("Give me SOC 2 CC6.1 evidence from this Splunk instance") and returns a complete audit package: xlsx findings tracker, debate transcript, executive memo, and a tamper-evident evidence chain — all in under 30 seconds.

**The core differentiator — three competing AI vendors debate every finding:**

Claude Sonnet 4 plays the Auditor, reading the control language literally. GPT-5.5 plays the Engineer, checking the SPL for statistical soundness. Gemini 2.5 Pro plays the Adversary, trying to disprove the PASS verdict and proposing counter-searches. These are independently-trained models from three competing labs — their disagreement is meaningful, not theatrical. Consensus rule: lowest verdict wins. One dissenting voice is enough to force a FAIL.

The Adversary doesn't just critique — it proposes follow-up SPL queries. Those execute automatically against live Splunk via MCP. A second panel round runs with the new evidence. The final verdict reflects what the data actually shows, not what the initial snapshot happened to capture.

**Five capabilities no other entry has:**

1. **Three-vendor independence** — Claude, GPT, and Gemini on separate OAuth-authenticated subscriptions (zero per-call API cost). Real disagreement between different models from different companies.

2. **Counter-evidence loop** — Adversary auto-runs follow-up SPL; Round 2 verdict supersedes Round 1 if new evidence changes the picture.

3. **Tamper-evident audit trail** — SHA-256 Merkle chain on every evidence snapshot. The xlsx carries the chain root. `aec verify` detects any post-collection edit in under 2 seconds.

4. **Native Splunk search command** — `| auditcompiler control=CC6.1` runs the three-vendor debate inline, returning `verdict`, `severity`, and `root_cause` as columns in Splunk's results table. Deployable as a Splunkbase app.

5. **Multi-framework efficiency** — `--control "SOC2:CC6.1+ISO:A.9.2.3+NIST-CSF:PR.AC-1"` in one prompt. The agent finds the minimal SPL set that covers all three frameworks simultaneously and produces a cross-framework gap report.

Additional capabilities: LangGraph orchestration with human-in-the-loop approval gates at SPL execution and verdict; drift detection comparing two audit windows; natural-language control resolution; checkpoint/resume after failures; live web dashboard at https://aec.accessquint.com.

**Built on real vCISO work:** The 36-control priors catalog (ISO 27001, NIST 800-53, NIST CSF, SOC 2, COBIT) was derived from 89 production consulting engagement templates — not generated from scratch. Every SPL hint, every control description, every remediation suggestion reflects patterns from real audits.

**The agent caught its own bug mid-debate:** During a live run, the Auditor persona identified that the sample time window used `-30d` relative dates against a 2018 dataset (BOTS v3). It recommended the fix in its critique. We accepted it. The follow-up run returned 1,247 events. That transcript ships with the repo.

---

## Live Demo URL

https://aec.accessquint.com

*(Pick a control from the dropdown, click Run — three AI vendors debate in real time. No install, no setup.)*

---

## GitHub Repository

https://github.com/sandhipveera/audit-evidence-compiler

---

## Track

Security

---

## Technologies Used (check all that apply)

- Splunk Enterprise (BOTS v3 dataset, 1.7M events)
- Splunk MCP Server (splunk/mcp-server-for-splunk — official)
- Splunk MCP Server (livehybrid/splunk-mcp — community)
- Splunk Custom Search Command (`| auditcompiler`)
- Claude Sonnet 4 (Anthropic) — via OAuth CLI + API fallback
- GPT-5.5 (OpenAI) — via Codex OAuth CLI + API fallback
- Gemini 2.5 Pro (Google) — via CLI + API fallback
- LangGraph (orchestration + HITL checkpointing)
- FastAPI + WebSocket (live web dashboard)
- Python 3.11

---

## Team

**Veera Sandiparthi**
Founder, AccessQuint LLC — cybersecurity consulting (vCISO practice)
Pleasanton, CA
reachveera2024@gmail.com

Solo submission. The vCISO priors catalog was derived from production consulting work across real SOC 2, ISO 27001, and NIST CSF engagements.

---

## How judges can evaluate it

**Option 1 — Live dashboard (easiest, 60 seconds):**
1. Go to https://aec.accessquint.com
2. Select "SOC 2 CC6.1 — MFA enforcement"
3. Click Run
4. Watch three AI vendors debate in real time

**Option 2 — Local run with sample data (5 minutes):**
```bash
git clone https://github.com/sandhipveera/audit-evidence-compiler
cd audit-evidence-compiler
pip install -e .
aec_demo --sample soc2-cc61
```

**Option 3 — Live Splunk via MCP (requires Docker):**
```bash
export SPLUNK_TOKEN="<token>"
docker compose -f infra/docker-compose.mcp.yml up -d
aec_demo --control CC6.1 --mcp official
```

**Option 4 — Splunk custom search command:**
Install `dist/auditcompiler-*.spl` into any Splunk Enterprise instance, then:
```spl
| auditcompiler control=CC6.1 mode=summary
```

---

## What the agent self-corrected (highlight for judges)

During a live demo run (transcript at `out/2026-05-25T022914Z.md` in the repo), the Auditor persona mid-debate identified a setup error: the SPL time window used `-30d` relative dates against a 2018 dataset (BOTS v3 data ends in September 2018). The agent recommended changing `earliest` to `2018-08-01`. We accepted the recommendation. The corrected run returned 1,247 real events. This is not a scripted demo beat — it happened organically during development and the transcript was preserved as-is.

---

## Additional notes for judges

- **Zero per-call LLM cost:** All three vendors run via OAuth-authenticated subscriptions (Claude Max, ChatGPT Team via Codex, Gemini CLI). The hackathon judges can observe this in `audit_trail.jsonl` — the `transport` field shows `anthropic-cli`, `openai-cli`, `gemini-cli` for each persona.
- **BOTS v3 time range:** All live Splunk queries use `earliest=2018-08-01 latest=2018-09-30` — the actual BOTS v3 data window. Using relative dates returns zero events.
- **INSUFFICIENT > FAIL** consensus rule is documented in the README and configurable via `AEC_INSUFFICIENT_OVERRIDES_FAIL=false`.
- **~12,000 lines** across 87 files, shipped across 15 PRs over the hackathon period.

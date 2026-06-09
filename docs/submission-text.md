# Hackathon Portal Submission Text
## Splunk Agentic Ops Hackathon 2026 — Security Track

Copy-paste each field directly into the submission form.

---

## Project Name

Audit Evidence Auto-Compiler

---

## One-Line Description (tweet-length)

An AI trust engine that converts a compliance question into Splunk evidence, runs a four-vendor adversarial panel debate — including Splunk's own Foundation-Sec-8B — and produces externally-verifiable, tamper-evident audit artifacts in under 30 seconds.

---

## Elevator Pitch (≤200 characters)

Four rival AI models — including Splunk's own Foundation-Sec-8B — debate your compliance evidence over real Splunk data, then seal a board-ready verdict in a tamper-evident chain anyone can verify.

---

## Project Description (500 words)

**The problem:** A SOC 2 audit cycle costs a vCISO 40+ hours. Most of that time is spent pulling evidence from Splunk, deciding whether it actually satisfies the control, reformatting it into auditor-acceptable artifacts, and writing the rationale. This is mechanical work — and it's exactly the kind of work that should be automated.

**What it does:** The Audit Evidence Auto-Compiler is an agentic pipeline that takes a compliance question ("Give me SOC 2 CC6.1 evidence from this Splunk instance") and returns a complete audit package: a board-ready Executive Compliance Report, xlsx findings tracker, debate transcript, and a tamper-evident evidence chain — all in under 30 seconds.

**The core differentiator — four vendors from four organizations debate every finding:**

Claude Sonnet 4 plays the Auditor (compliance lens). GPT-5.5 plays the Engineer (statistical lens). Gemini 2.5 Pro plays the Adversary (red-team lens). And **Foundation-Sec-8B** — Splunk/Cisco's own open-source security model trained specifically on cybersecurity data — plays the Security Model, applying a threat-intelligence lens: not "does this satisfy the control language?" but "does this control actually stop real attackers?" These are independently-trained models from four competing organizations. Consensus rule: lowest verdict wins. One dissenting voice forces PARTIAL or FAIL.

The Adversary doesn't just critique — it proposes follow-up SPL queries. Those execute automatically against live Splunk via MCP. A second panel round runs with the new evidence. The final verdict reflects what the data actually shows, not what the initial snapshot happened to capture.

**Seven capabilities no other entry has:**

1. **Four-vendor independence including Splunk's own model** — Claude, GPT, Gemini, and Foundation-Sec-8B from four different organizations, four different training sets. Zero per-call API cost (OAuth subscriptions + HuggingFace hosted inference).

2. **Counter-evidence loop** — Adversary auto-runs follow-up SPL; Round 2 verdict supersedes Round 1 if new evidence changes the picture.

3. **Tamper-evident audit trail + external verifier** — SHA-256 Merkle chain on every evidence snapshot. The xlsx carries the chain root. `aec verify` detects any post-collection edit in under 2 seconds. External auditors verify the chain without installing anything at [aec3.accessquint.com/verify](https://aec3.accessquint.com/verify).

4. **Native Splunk search command** — `| auditcompiler control=CC6.1` runs the four-vendor debate inline, returning `verdict`, `severity`, and `root_cause` as columns in Splunk's results table. Deployable as a Splunkbase app.

5. **Multi-framework efficiency** — `--control "SOC2:CC6.1+ISO:A.8.2+NIST-CSF:PR.AC-1"` in one prompt. The agent finds the minimal SPL set that covers all three frameworks simultaneously and produces a cross-framework gap report.

6. **SOC incident response integration** — When a Splunk alert fires (brute force, MFA bypass, privilege escalation), the agent maps the alert to affected compliance controls, runs the four-vendor panel debate, and produces an incident-linked audit report automatically in the same window the SOC analyst is triaging.

7. **Board-ready Executive Compliance Report** — every run auto-generates the one-page report a vCISO hands to the board: a letter-grade ring, per-framework posture (coverage %, strength, verdict) across all five frameworks, a control-maturity trend vs. the client's prior assessments, and prioritized findings with remediation (PDF + xlsx tracker). Grades and remediation come from AccessQuint's real vCISO engagements, not invented.

Additional capabilities: LangGraph orchestration with human-in-the-loop gates at SPL execution and verdict; drift detection across two audit windows; an ISO 42001 / NIST AI RMF AI-governance scenario; checkpoint/resume after failures; the live Tessera dashboard at https://aec3.accessquint.com.

**Built on real vCISO work:** The 36-control priors catalog (ISO 27001, NIST 800-53, NIST CSF, SOC 2, COBIT) was derived from 89 production consulting engagement templates — not generated from scratch. Every SPL hint, control description, and remediation reflects patterns from real audits.

**The agent caught its own bug mid-debate:** mid-run, the Auditor persona flagged that the sample time window used `-30d` relative dates against the 2018 BOTS v3 dataset; we accepted its fix and the corrected run returned 1,247 real events. That transcript ships with the repo (full account below).

---

## Live Demo URL

https://aec3.accessquint.com

*(Pick a control from the dropdown, click Run — four AI voices debate in real time. No install, no setup.)*

---

## GitHub Repository

https://github.com/sandhipveera/audit-evidence-compiler

---

## Track

Security

---

## Technologies Used (check all that apply)

- Splunk Enterprise (BOTS v3 dataset, 1.94M events)
- Splunk MCP Server (splunk/mcp-server-for-splunk — official)
- Splunk MCP Server (livehybrid/splunk-mcp — community)
- Splunk Custom Search Command (`| auditcompiler`)
- Claude Sonnet 4 (Anthropic) — via OAuth CLI + API fallback
- GPT-5.5 (OpenAI) — via Codex OAuth CLI + API fallback
- Gemini 2.5 Pro (Google) — via CLI + API fallback
- **Foundation-Sec-8B (Cisco/Splunk)** — via HuggingFace Inference API (Featherless.ai provider), `fdtn-ai/Foundation-Sec-8B-Instruct`
- LangGraph (orchestration + HITL checkpointing)
- FastAPI + WebSocket (live web dashboard + auditor verification portal)
- SHA-256 Merkle chaining (tamper-evident evidence trail)
- Cloudflare Tunnel (zero-open-ports HTTPS deployment)
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
1. Go to https://aec3.accessquint.com
2. Select "SOC 2 CC6.1 — MFA enforcement"
3. Click **Guided Walkthrough** — replays a real run instantly, no setup or code needed
4. Watch four AI vendors debate (Claude + GPT + Gemini + Foundation-Sec-8B) → verdict → Merkle seal → posted back to Splunk

*Want a fresh, genuinely live run?* Use **Run live** instead — a real four-vendor run against Splunk (~1–3 min). Live runs are **code-gated** (a run token, with a daily cap) to control LLM cost and abuse. **Request a live-run code at reachveera2024@gmail.com** — the Guided Walkthrough above needs none.

**Option 1b — Auditor verification portal (30 seconds):**
1. Go to https://aec3.accessquint.com/verify
2. Download the `audit_trail.jsonl` from a completed run (available in the repo at `out/`)
3. Drag-and-drop the file onto the verification page
4. See the green VERIFIED banner + per-snapshot chain-of-custody proof

**Option 2 — Local run with sample data (5 minutes):**
```bash
git clone https://github.com/sandhipveera/audit-evidence-compiler
cd audit-evidence-compiler
pip install -e .
export HF_TOKEN="<huggingface-token>"  # optional; without it the panel degrades to 3 personas
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

- **Hosted Models use:** The fourth persona uses `fdtn-ai/Foundation-Sec-8B-Instruct` through HuggingFace Hosted Inference on Featherless.ai. Judges can observe this in `audit_trail.jsonl` — the fourth critique records `persona: security_model` and `transport: foundation-sec-api` when `HF_TOKEN` is configured.
- **Zero per-call commercial LLM cost:** Claude, GPT, and Gemini run via OAuth-authenticated subscriptions (Claude Max, ChatGPT Team via Codex, Gemini CLI), with API-key fallbacks for contributors.
- **BOTS v3 time range:** All live Splunk queries use `earliest=2018-08-01 latest=2018-09-30` — the actual BOTS v3 data window. Using relative dates returns zero events.
- **INSUFFICIENT > FAIL** consensus rule is documented in the README and configurable via `AEC_INSUFFICIENT_OVERRIDES_FAIL=false`.
- **~12,000 lines** across 87 files, shipped across 15 PRs over the hackathon period.

---
marp: true
theme: gaia
class: lead
paginate: true
backgroundColor: #0c0b12
color: #ece6da
style: |
  section { font-family: 'Inter', sans-serif; }
  h1, h2 { color: #dab36a; }
  strong { color: #dab36a; }
  a { color: #ec3b9b; }
  code { background: #1d1b27; color: #6fd44a; }
  .small { font-size: 0.8em; color: #9a9384; }
---

<!-- Render: `marp docs/slide-deck.md --pdf` (or --pptx / --html). Speaker notes are in HTML comments. -->

# Tessera
## The trust engine for AI-generated compliance evidence

**Four rival AI models debate real Splunk data, then seal a tamper-evident verdict you can verify.**

<span class="small">Splunk Agentic Ops Hackathon 2026 · Security Track · live at aec3.accessquint.com</span>

<!-- One sentence: Tessera turns a compliance question into Splunk-sourced, AI-cross-checked, tamper-evident audit evidence — in 30 seconds. -->

---

## The problem

- A single **SOC 2 audit cycle = 40+ hours** of a vCISO pulling evidence from Splunk, judging whether it satisfies the control, and reformatting it for auditors.
- **AI can generate that evidence in seconds — but can you *trust* it?** One model, one opinion, no proof it wasn't altered.
- **90% of orgs fail their first AI-governance audit** — they have policies, not proof.

> The bottleneck isn't generating evidence. It's *trusting* it.

---

## The idea: don't trust one AI — make four debate

Four **independently-trained models from four competing vendors** each take a role:

| Persona | Model | Lens |
|---|---|---|
| **Auditor** | Claude (Anthropic) | does it satisfy the control language? |
| **Engineer** | GPT-5.5 / Codex (OpenAI) | is it statistically sound? |
| **Adversary** | Gemini (Google) | red-team — can an attacker slip through? |
| **Security Model** | **Foundation-Sec-8B** (Splunk/Cisco) | does it stop *real* attackers? |

**Consensus is mechanical:** lowest verdict wins, no LLM tiebreaker → **reproducible**. One dissent forces PARTIAL/FAIL.

---

## Real Splunk, end to end

1. Compliance question → **SPL generated** → **policy gate** → executed against **Splunk BOTS v3 (1.94M real events)** via **MCP**.
2. Four vendors debate the returned evidence. The **Adversary can issue counter-SPL** — a second round runs on what the data *actually* shows.
3. Verdict + Merkle root **posted back to Splunk** (`HEC → index=aec_audit`) — a live audit log *inside the platform*.
4. Splunk-native **Compliance Posture dashboard** + `| auditcompiler` search command.

<span class="small">Not mocked: live tunnel to the Splunk instance, real query results, real write-back.</span>

---

## Tamper-evident by construction

- Every evidence snapshot is **SHA-256 Merkle-chained** (canonical JSON, no signing keys to leak).
- The `gap_report.xlsx` carries the **chain root**.
- **Anyone can verify** — drag the `audit_trail.jsonl` into the public **/verify** portal:
  - ✅ **VERIFIED** — nothing changed since collection.
  - ❌ **TAMPERED** — one flipped verdict breaks the hash.

> Trust isn't asserted. It's *provable*, by a third party, with no install.

---

## Four ways teams use it

1. **40 hours → 30 seconds** — SOC 2 CC6.1 audit prep, compiled in one run.
2. **Passed in Q1, failing now** — control *drift* across two Splunk windows (point-in-time audits miss 365 days).
3. **Verify, then trust** — four rival models cross-check every verdict; tampering is provable.
4. **AI-governance audit (ISO 42001)** — "policies, not proof" → provable AI-control evidence from Splunk.

<span class="small">All five frameworks: SOC 2 · ISO 27001:2022 · NIST CSF 2.0 · NIST 800-53 Rev 5 · COBIT 2019.</span>

---

## Architecture

```
Trigger (control / Splunk alert)
   ↓
LangGraph agent:  map → SPL-gen → policy gate → MCP executor → normalize
   ↓
Four-vendor panel (parallel) → mechanical consensus (lowest-of-four)
   ↓
Merkle seal (SHA-256) → xlsx + audit_trail.jsonl
   ↓
Post back to Splunk (HEC → index=aec_audit) · external /verify portal
```

<span class="small">Pluggable MCP transport (official | livehybrid | rest). Personas are plain markdown — edit to change behavior.</span>

---

## Why it wins (the four criteria)

- **Technological Implementation** — real multi-vendor orchestration, live Splunk via MCP, policy-gated SPL, Merkle integrity, write-back to Splunk.
- **Design / UX** — a two-panel *tool* (not a marketing page): pick a case, convene the tribunal, watch four models rule, jump to the board report.
- **Potential Impact** — collapses 40+ audit hours to 30 seconds across 5 frameworks; continuous (drift) not point-in-time.
- **Quality of Idea** — "verify, then trust": adversarial cross-vendor consensus + third-party-verifiable proof. Novel and reproducible.

---

## See it live

- **Dashboard:** `aec3.accessquint.com`
- **Splunk (read-only):** `splunk-aec.accessquint.com` — Compliance Posture dashboard + `index=aec_audit`
- **Verify portal:** `aec3.accessquint.com/verify` — try the valid vs. tampered sample trail
- **Code:** `github.com/sandhipveera/audit-evidence-compiler`

# Tessera
### Real Splunk. Four rival models. One verdict you can prove.

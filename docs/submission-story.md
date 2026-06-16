# Tessera — Audit Evidence Auto-Compiler

*A trust engine for AI-generated compliance evidence.*

---

## Inspiration

I run a vCISO practice. A single SOC 2 cycle eats **40+ hours** of an expert's time — and almost none of it is judgment. It's *mechanical*: pull the right evidence out of Splunk, decide whether it actually satisfies the control language, reformat it into something an auditor will accept, and write the rationale. That is exactly the kind of work that should be automated.

So why hadn't it been? Because of one hard problem: **trust**. An LLM can draft beautiful compliance evidence in seconds — and no auditor on earth will accept *"an AI said so."* AI evidence has two failure modes that kill it in a real audit:

1. **Plausible but wrong.** A single model is confidently agreeable. It will happily call a control PASS that a red-teamer would tear apart.
2. **Unprovable.** Even if the verdict is right, you can't prove the evidence wasn't quietly edited after the fact.

Tessera is the answer to both. Instead of *one* model you have to trust, **four rival models from four different organizations debate every finding** — and the entire chain of evidence is **cryptographically sealed** so any tampering is detectable by anyone, with no special tools. The goal: AI compliance evidence you can hand straight to an auditor, because every claim in it is *provable*.

---

## What it does

You give it a compliance question — *"Give me SOC 2 CC6.1 evidence from this Splunk instance"* — and ~30 seconds later you get a complete, board-ready audit package:

- A **four-vendor debate transcript** with a reconciled verdict.
- A **board-ready Executive Compliance Report**: letter-grade ring, per-framework posture, a maturity trend against prior assessments, and prioritized remediation.
- A **gap-findings tracker** (xlsx) and a **tamper-evident evidence chain** (`audit_trail.jsonl`) anyone can verify at `/verify`.

Before the panel ever weighs in, **Splunk's own machine learning scores the evidence for anomalies** — so the verdict is shaped by Splunk's AI, not just external LLMs.

All of it runs against **real Splunk BOTS v3 data — 1.94M events**, not a mock.

---

## How we built it

The whole thing is one **LangGraph** agent that walks a fixed pipeline:

```
mapper → spl-gen → validator → mcp → splunk-ml (MLTK) → panel → consensus → formatter → merkle
```

**Splunk's own AI runs inside the loop — at runtime.** Pulling rows out of Splunk isn't the same as *using Splunk's AI*, so the `splunk-ml` step puts Splunk's in-platform machine learning on the critical path. When the **Splunk Machine Learning Toolkit (MLTK)** is installed it runs `fit DensityFunction` / `apply`; otherwise it falls back to the built-in `anomalydetection` command (the engine behind the Splunk App for Anomaly Detection). On the BOTS v3 DNS telemetry it flags domains with anomalous query-cardinality — classic tunnelling/exfiltration signals like `outlook.com` and `in-addr.arpa` — and hands those Splunk-scored anomalies to all four vendors as corroborating evidence. **Splunk's AI, not just the external LLMs, shapes every verdict.**

The division of labor, in one line: **Splunk's MLTK is the smoke detector; the four-vendor panel is the fire marshal who decides whether the building passes inspection; and the Merkle chain is the signed certificate no one can forge.** Splunk's ML surfaces the signal — Tessera turns it into a reconciled, provable, auditor-ready verdict.

**The four-vendor panel is the heart of it.** Four independently-trained models, each given a different persona (the personas are plain markdown — edit a file, change the behavior, no recompile):

| Vendor | Persona | Lens |
|---|---|---|
| Claude Sonnet 4 | Auditor | does this satisfy the control *language*? |
| GPT-5.5 | Engineer | is the evidence *statistically* sufficient? |
| Gemini 2.5 Pro | Adversary | how does this *fail* against a real attacker? |
| **Foundation-Sec-8B** (Splunk/Cisco) | Security Model | does this control actually *stop* the threat? |

**Consensus is mechanical, not an LLM tiebreaker** — a deliberate choice for reproducibility. Each verdict gets a severity rank:

$$
s(\text{PASS}) = 0 < s(\text{PARTIAL}) = 1 < s(\text{FAIL}) = 2 < s(\text{INSUFFICIENT}) = 3
$$

and the panel verdict is simply the most severe voice in the room:

$$
V_{\text{panel}} \;=\; \arg\max_{p \,\in\, \{\text{auditor},\,\text{engineer},\,\text{adversary},\,\text{security}\}} \; s\!\left(v_p\right)
$$

One dissenting model forces `PARTIAL` or `FAIL`. `INSUFFICIENT` outranks `FAIL` on purpose — *"I don't have enough evidence"* is a worse audit outcome than a clean failure. No averaging, no model breaking the tie, so the same evidence always yields the same verdict.

**Why four vendors instead of one model playing four roles?** Independence. If a single real gap is missed by any one model with probability $p$, then under (idealized) independence the chance *all four* miss it is

$$
P(\text{all four miss}) = \prod_{i=1}^{4} p_i \;\approx\; p^4
$$

At a generous $p = 0.3$ per model, that's $0.3^4 \approx 0.008$ — roughly a 37× improvement over a single reviewer. The independence isn't perfect (the models share a planet's worth of training text), but four *different* organizations and training sets is structurally far better than one model wearing four hats.

**The Adversary can fight back.** It's the only persona allowed to emit follow-up SPL — and only for one round, so there are no infinite loops. Those counter-searches must pass an SPL policy gate (allowed indexes, forbidden commands) before they execute against live Splunk via **MCP**. A second panel round then runs on the new evidence, so the final verdict reflects what the data *actually* shows.

**Tamper-evidence is a Merkle chain over canonical JSON.** Every evidence snapshot is hashed in sequence:

$$
h_0 = \mathrm{SHA256}\big(\mathrm{canon}(e_0)\big), \qquad
h_i = \mathrm{SHA256}\big(h_{i-1} \,\Vert\, \mathrm{canon}(e_i)\big)
$$

where $\mathrm{canon}(\cdot)$ is JSON with sorted keys and no whitespace, and $\Vert$ is concatenation. Edit any snapshot $e_j$ and you change $h_j$ — and therefore every $h_i$ for $i > j$, all the way to the root. `aec verify` recomputes the chain in under two seconds and flags the break. No signing keys, no infrastructure — just pure SHA-256 anyone can reproduce.

**Splunk-native, three ways.** Evidence is *pulled* with generated, policy-gated SPL over a runtime-pluggable MCP transport (`official | livehybrid | rest`); it's *scored* in-platform by Splunk's own ML (MLTK `fit`/`apply`, else `anomalydetection`); and the verdict is posted *back* into Splunk via HEC (`index=aec_audit`). The whole debate is also exposed as a custom search command: `| auditcompiler control=CC6.1`.

**Built on real work.** The 36-control priors catalog (SOC 2, ISO 27001:2022, NIST CSF 2.0, NIST 800-53, COBIT) was distilled from **89 production consulting engagement templates** — every SPL hint and remediation reflects a pattern from a real audit. The stack: Python 3.11, LangGraph, FastAPI + WebSocket, and Cloudflare Tunnel for zero-open-ports public HTTPS.

---

## Challenges we ran into

- **Getting a genuinely fourth vendor.** Foundation-Sec-8B is the differentiator, but `fdtn-ai/Foundation-Sec-8B-Instruct` is served by exactly *one* hosted-inference provider (Featherless). Cold starts threw `402/403/503` mid-demo. The fix was operational (pre-warm the endpoint before a live run) plus a safety net: a single-vendor fallback so the panel degrades to Claude-only rather than crashing — and is honest in the audit trail about which transport actually answered.
- **Determinism vs. LLMs.** A tamper-evident chain is worthless if the same input hashes differently each run. That's why consensus is mechanical and why hashing uses *canonical* JSON — sorted keys, no whitespace — so byte-for-byte reproducibility holds across machines.
- **Letting an adversary write queries — safely.** Allowing the Adversary to emit live SPL is powerful and dangerous. The SPL policy gate (allowlisted indexes, forbidden commands) plus the one-round cap was the compromise between "real counter-evidence" and "no runaway agent."
- **Real data bites back.** BOTS v3 is a *2018* dataset. The first runs used `-30d` relative time windows and returned **zero events** — a setup bug hiding in plain sight (see the next section for how it got caught).
- **Shipping it publicly.** Splunk emits `http://` self-redirects behind a TLS-terminating Cloudflare Tunnel; getting clean public HTTPS (zero open ports) with read-only judge access took real plumbing.

---

## Accomplishments that we're proud of

- **The agent caught its own bug, live.** During an actual debate, the **Auditor persona** flagged that the `-30d` window was pointed at a 2018 dataset, recommended pinning `earliest=2018-08-01`, the fix was accepted, and the corrected run returned **1,247 real events**. It wasn't scripted — that unedited transcript ships with the repo. An agent self-correcting a human's setup error mid-run is the moment the whole idea clicked.
- **Four genuinely independent vendors — including Splunk's own model — on real data.** Not one model wearing four hats: Claude, GPT-5.5, Gemini, and Foundation-Sec-8B, debating over **1.94M real BOTS v3 events**.
- **Splunk's AI is on the critical path, at runtime.** The Splunk Machine Learning Toolkit (`fit DensityFunction`/`apply`, with `anomalydetection` fallback) scores the evidence for anomalies *inside Splunk* before the panel debates — so the platform's own AI, not just external LLMs, informs every verdict.
- **Provability with zero infrastructure.** A full SHA-256 Merkle chain and a drag-and-drop public verifier that returns `VERIFIED` / `TAMPERED` — no keys, no accounts, no install.
- **It's live and Splunk-native.** Public read-only judge access, the verdict written *back* into Splunk (`index=aec_audit`), and the entire tribunal callable inline as `| auditcompiler`.

---

## What we learned

- **Determinism is a feature, not a constraint.** The instinct with agents is to let an LLM arbitrate everything. But the moment you need *reproducibility* — a hash that matches, a verdict that's defensible — pushing judgment out of the model and into mechanical rules (severity ordering, canonical encoding) is what makes the output trustworthy. The models *debate*; the math *decides*.
- **Trust in AI output is an architecture problem, not a prompt problem.** You don't make AI evidence credible with a better prompt. You make it credible with **independence** (rival vendors) and **provability** (cryptographic chains) — structural properties you design in.
- **Disagreement is the signal.** The most valuable runs are the ones where the Adversary dissents and forces a `FAIL` the other three would have waved through. A panel that always agrees has told you nothing.
- **The boring 20% is the demo.** Cold-start flakiness, redirect loops, time-window bugs — the cryptography and the multi-agent graph were *easier* than making a live, real-data system reliable enough to put in front of judges.

---

## What's next for Tessera - Audit Evidence Auto-Compiler

- **Whole-framework batch runs** — one prompt that walks every control in SOC 2 or ISO 27001 and assembles a single cross-framework gap report.
- **Scheduled drift alerting** — run the same control across two audit windows on a cron and fire a Splunk alert when a control that passed quietly regresses.
- **Splunkbase packaging** — ship `| auditcompiler` as an installable app so any Splunk Enterprise instance can convene the four-vendor tribunal inline.
- **More security models in the panel** — the architecture is vendor-pluggable; adding a fifth or sixth independent model only strengthens the independence argument above.

---

**Live:** [aec3.accessquint.com](https://aec3.accessquint.com) · **Verify:** [aec3.accessquint.com/verify](https://aec3.accessquint.com/verify) · **Code:** [github.com/sandhipveera/audit-evidence-compiler](https://github.com/sandhipveera/audit-evidence-compiler)

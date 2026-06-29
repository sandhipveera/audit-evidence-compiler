# LinkedIn posts — Tessera (Splunk Agentic Ops Hackathon 2026)

Two ready-to-post drafts announcing the Tessera submission in the Devpost gallery.
Devpost-only call-to-action. Winners announced **July 17, 2026 at 2pm PT**.

Submission: https://devpost.com/software/tessera-audit-evidence-auto-compiler

---

## Company post

🏛️ A single SOC 2 cycle eats 40+ hours of an expert's time — and almost none of it is judgment. It's mechanical: pull the right evidence out of Splunk, decide whether it satisfies the control, reformat it, write the rationale.

So why hasn't it been automated? One word: **trust**. An LLM can draft beautiful compliance evidence in seconds — and no auditor on earth will accept *"an AI said so."*

That's the problem we built **Tessera** to solve, for the **Splunk Agentic Ops Hackathon 2026**.

Instead of one model you have to trust, **four rival AI models from four competing labs debate every finding** — each with its own job:

🔵 **Claude** — the Auditor (does it satisfy the control language?)
🟢 **GPT-5.5** — the Engineer (is the evidence statistically sufficient?)
🟠 **Gemini 2.5 Pro** — the Adversary (how does this fail against a real attacker?)
🟣 **Foundation-Sec-8B** (Splunk's own) — the Security Model (does the control actually stop the threat?)

Before they ever weigh in, **Splunk's own machine learning (MLTK) scores the evidence for anomalies** — so the platform's AI shapes the verdict too.

Ask it one question — *"Give me SOC 2 CC6.1 evidence from this Splunk instance"* — and ~30 seconds later you get a board-ready audit package: a four-vendor debate transcript, an executive compliance report, a gap tracker, and a **SHA-256 Merkle-chained, tamper-evident evidence chain anyone can verify offline — no keys, no install.**

All of it runs against **real Splunk BOTS v3 data — 1.94M events**, not a mock.

AI compliance evidence you can hand straight to an auditor, because every claim in it is *provable.*

🔗 Take a look in the gallery: https://devpost.com/software/tessera-audit-evidence-auto-compiler

Winners are announced **July 17, 2026 at 2pm PT** — fingers crossed. 🤞 Would love your thoughts in the comments.

#Splunk #Claude #GPT5 #Gemini #FoundationSec #AgenticAI #Compliance #SOC2 #Cybersecurity #AI #Hackathon #AuditTech #vCISO

---

## Personal post (short)

We all know this pain: a SOC 2 cycle burns 40+ hours of expert time — and almost none of it is actual judgment.

So I built **Tessera** for the Splunk Agentic Ops Hackathon 2026.

The trick is trust. No auditor accepts *"an AI said so."* So Tessera doesn't use one AI — it uses **four rival models from four competing labs**, each with its own job, debating every finding over **real Splunk data**:

🔵 **Claude** — the Auditor (does it satisfy the control language?)
🟢 **GPT-5.5** — the Engineer (is the evidence statistically sufficient?)
🟠 **Gemini 2.5 Pro** — the Adversary (how does this fail against a real attacker?)
🟣 **Foundation-Sec-8B** — the Security Model (does the control actually stop the threat?)

Then it seals their verdict in a tamper-evident chain anyone can verify.

The way I think about it:
🔥 **Splunk's ML is the smoke detector** — it surfaces the anomaly.
🚒 **The four-vendor panel is the fire marshal** — it decides whether the building passes inspection.
📜 **The Merkle chain is the signed certificate** — no one can forge it.

AI compliance evidence you can hand straight to an auditor, because every claim is *provable.*

Since submitting I've gone deeper: you can now watch *exactly* where the four models disagree — a live dissent ledger with a panel-agreement confidence score — while every vendor's verdict, latency and dissent streams back into Splunk as searchable evidence, now mapped to MITRE ATT&CK.

🔗 https://devpost.com/software/tessera-audit-evidence-auto-compiler

Winners announced July 17, 2pm PT. 🤞

#Splunk #Claude #GPT5 #Gemini #FoundationSec #AgenticAI #Compliance #SOC2 #Cybersecurity

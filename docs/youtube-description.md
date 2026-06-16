# YouTube — Demo Video Metadata

## Suggested title
Tessera — Four Rival AI Models Audit Your Splunk Data (and Prove It) | Splunk Agentic Ops Hackathon 2026

## Description (copy-paste below the line)

---

Four rival AI models — including Splunk's own Foundation-Sec-8B — debate your compliance evidence over real Splunk data, then seal a board-ready verdict in a tamper-evident chain anyone can verify.

Tessera (the Audit Evidence Auto-Compiler) turns one compliance question into a complete, board-ready audit package in ~30 seconds. One LangGraph agent pulls real Splunk BOTS v3 data (1.94M events), puts it in front of four independently-trained models from four competing vendors — Claude (Auditor), GPT-5.5 (Engineer), Gemini 2.5 Pro (Adversary), and Foundation-Sec-8B (Security Model) — and seals their verdict into a SHA-256 Merkle chain. Before the panel debates, Splunk's own machine learning — the Splunk Machine Learning Toolkit (MLTK) — scores that evidence for anomalies in-platform, so the platform's AI shapes the verdict too. Consensus is mechanical — the most severe verdict wins, no LLM tiebreaker — so it's fully reproducible. Anyone can re-check the chain offline, with no keys and no install.

⏱ Chapters  (⚠ re-sync these timestamps to the final re-recorded cut — values below assume the MLTK beat lands ~0:27)
0:00 — The trust problem + architecture
0:27 — Splunk's own MLTK scores the evidence (in-platform anomaly scan)
0:40 — Convene the tribunal (SOC 2 CC6.1)
0:54 — Four rival models debate the evidence
1:27 — Verdict, Merkle seal & board-ready Executive Report
1:55 — Verify portal: VERIFIED vs TAMPERED
2:38 — Inside Splunk: live Compliance Posture dashboard

🔗 Try it yourself
• Live dashboard: https://aec3.accessquint.com
• Auditor verify portal: https://aec3.accessquint.com/verify
• Source code: https://github.com/sandhipveera/audit-evidence-compiler

🛠 Built with
Splunk Enterprise (BOTS v3) · Splunk Machine Learning Toolkit (MLTK — fit/apply anomaly scoring) · Splunk MCP servers (official + livehybrid) · custom `| auditcompiler` search command · LangGraph · Claude · GPT-5.5 · Gemini 2.5 Pro · Foundation-Sec-8B · FastAPI + WebSocket · SHA-256 Merkle chaining · Cloudflare Tunnel · Python 3.11

Built solo for the Splunk Agentic Ops Hackathon 2026 (Security Track), on a real vCISO priors catalog — 36 controls across SOC 2, ISO 27001, NIST CSF, NIST 800-53, and COBIT.

#Splunk #MLTK #AI #MachineLearning #Compliance #SOC2 #Cybersecurity #LangGraph #AgenticAI #AuditTech #FoundationSec #MerkleTree

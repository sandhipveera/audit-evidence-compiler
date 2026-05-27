# 23-day roadmap to submission

## Status

| Task | Status | Notes |
|---|---|---|
| 001 — Priors parser | ✅ done (kickoff) | 36 controls across 5 frameworks |
| 004 — xlsx formatter | ✅ merged | 620 LOC |
| 007 — Panel debate | ✅ merged | 3 vendors × OAuth CLIs, 23 tests |
| 008 — Merkle chain | ✅ merged | 35 integrity tests |
| 009 — Panel must-fixes | ✅ merged | max_tokens, transcript persist, severity doc |
| 010 — Splunk REST | ✅ merged | Initial Splunk client + samples |
| 011 — xlsx + Merkle wire | ✅ merged | 4 artifacts in one run |
| 011b — Transport bug fixes | ✅ merged | Claude is_error + Codex gpt-5.5 |
| 012 — Live Splunk + BOTS v3 | ✅ merged + ingested | 1.7M events queryable |
| **013 — Splunk MCP (dual server)** | ✅ merged | Headline hook, both servers, runtime switch |
| **014 — Counter-evidence loop** | ✅ merged | 2-round debate, adversary counter-searches |
| **015 — Drift detection** | ✅ merged | Two-window comparison, DriftAnalysis model |
| **016 — LangGraph wrapper + HITL** | ✅ merged | 8-node graph, HITL gates, checkpointing |
| **017 — Differential framework mapping** | ✅ merged | One prompt → 3 frameworks, minimal SPL set |
| **018 — Splunk app package** | ✅ merged | `\| auditcompiler` custom search command, .spl |
| **019 — Web dashboard** | ✅ merged | FastAPI + WebSocket, aec.accessquint.com |
| **020 — Foundation-Sec-8B (4th persona)** | ✅ merged (PR #18) | HuggingFace Featherless.ai, threat-intel lens, $1K Hosted Models bonus |
| **021 — SOC Incident Response mode** | ✅ merged (PR #16) | Alert→controls mapping, `/api/incident` webhook, `--mode incident` CLI |
| **022 — Compliance Posture Dashboard** | ⏳ not started | Splunk Dashboard Studio JSON, 36-control scorecard |
| **023 — Auditor Verification Portal** | ✅ merged (PR #19) | `/verify` page, drag-and-drop JSONL upload, VERIFIED/TAMPERED banner |
| Architecture diagram refresh | ⏳ end | Add 4th vendor, verification portal, SOC incident mode, Cloudflare Tunnel |
| README rewrite | ✅ done | Trust-engine framing, 6 differentiators, Foundation-Sec panel |
| 3-min demo video | ⏳ end | Re-record with lowest_of_four after GPT quota reset |
| Submission ceremony | ⏳ end | Devpost form using docs/submission-text.md |

## ✅ STRETCH TIER COMPLETE — Day 2 of 23 (2026-05-26)

Prize-targeting additions merged overnight:
- PR #16: SOC Incident Response mode (alert webhook + incident compliance report)
- PR #18: Foundation-Sec-8B as 4th panel persona (HuggingFace / Featherless.ai) — wins $1K Hosted Models bonus
- PR #19: Auditor Verification Portal at aec.accessquint.com/verify — external chain-of-custody proof

Remaining: task 022 (Compliance Posture Dashboard), architecture diagram refresh, demo re-record (4-vendor), submission.

## Current panel status (2026-05-26)

- Claude Sonnet 4 (Auditor) ✅ — anthropic-cli, OAuth
- GPT-5.5 (Engineer) ⚠️ — ChatGPT Team quota resets at 08:48 AM PT daily; currently `lowest_of_three` until reset
- Gemini 2.5 Pro (Adversary) ✅ — gemini-cli, OAuth
- Foundation-Sec-8B (Security Model) ✅ — foundation-sec-api, HF_TOKEN required, gives PARTIAL (ATT&CK lens)

After quota reset: `rm -f out/* && aec_demo --sample soc2-cc61` to confirm `lowest_of_four`.

## Critical path (most pessimistic)

Even if 014 + 015 + 016 + part of 018 land, you have:
- Three-vendor OAuth panel ✅
- Merkle-sealed audit artifacts ✅
- Live Splunk via MCP (dual server) ✅
- Counter-evidence recurrence ✅
- Drift detection ✅
- LangGraph orchestration ✅
- Splunk app stub (registers `| auditcompiler` command) ✅

That alone is a winning submission. 017, 019, full 018 are amplifiers.

## Estimated LOC at submission

- Already merged: ~5,000 LOC
- 014: +300
- 015: +250
- 016: +400
- 017: +200
- 018: +500 + conf files
- 019: +600
- Final: ~7,250 LOC of substance

## Tier statuses

- [x] Tier 1 (table-stakes): 011 + 012 + 013 — ALL DONE
- [x] Tier 2 (winners): 014 + 015 + 016 — ALL DONE (merged 2026-05-25)
- [x] Tier 3 (moonshot): 017 + 018 + 019 — ALL DONE (merged 2026-05-25)
- [ ] Submission ceremony

## Notes / observations

- **Cross-vendor reviewer (Codex) catches real bugs every time.** Keep `REVIEWER=codex` on for all remaining tasks. Cost: 0 (subscription-paid). Value: ~1 silent bug per task.
- **Panel debate already self-corrects.** Task 014's value-add is making that visible to judges; the underlying reasoning is already there.
- **BOTS v3 is 2018 data.** All sample time ranges must use `earliest=2018-08-01 latest=2018-09-30` for live runs. Documented in `samples/*.json`.
- **Don't forget the demo transcript from 2026-05-25T022914Z.md.** That's the gold artifact showing the agent caught its own setup bug. Should appear in the README + demo video.

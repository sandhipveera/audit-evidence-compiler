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
| **014 — Counter-evidence loop** | 📝 spec'd, ready to launch | The cinematic 2-round debate |
| **015 — Drift detection** | 📝 spec'd | Continuous-compliance story |
| **016 — LangGraph wrapper + HITL** | 📝 spec'd | Makes "agentic" claim rigorous |
| **017 — Differential framework mapping** | 📝 spec'd | One prompt → 3 frameworks |
| **018 — Splunk app package** | 📝 spec'd | The moonshot; ship in 3 stages |
| **019 — Web dashboard** | 📝 spec'd | Stretch — only if everything else lands |
| Architecture diagram refresh | ⏳ end | Match final shipped reality |
| README rewrite | ⏳ end | Front-load the differentiators |
| 3-min demo video | ⏳ end | Tmux split + iterative takes |
| Submission ceremony | ⏳ end | Hackathon portal form |

## Recommended launch order for Tier 2 + 3

Each waits for the previous to merge (panel.py overlap risks):

1. **014 (counter-evidence loop)** — strongest narrative beat addition
2. **016 (LangGraph wrapper)** — second; LangGraph state model is referenced by some 015 internals
3. **015 (drift detection)** — third; benefits from graph state structure
4. **017 (differential framework mapping)** — fourth; can stretch to parallel-with-018 since they touch different files
5. **018 (Splunk app package)** — stage A first (validates app loading), stage B + C if time allows
6. **019 (web dashboard)** — only if 018 lands by day 18

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
- [ ] Tier 2 (winners): 014 + 015 + 016
- [ ] Tier 3 (moonshot): 017 + 018 + 019
- [ ] Submission ceremony

## Notes / observations

- **Cross-vendor reviewer (Codex) catches real bugs every time.** Keep `REVIEWER=codex` on for all remaining tasks. Cost: 0 (subscription-paid). Value: ~1 silent bug per task.
- **Panel debate already self-corrects.** Task 014's value-add is making that visible to judges; the underlying reasoning is already there.
- **BOTS v3 is 2018 data.** All sample time ranges must use `earliest=2018-08-01 latest=2018-09-30` for live runs. Documented in `samples/*.json`.
- **Don't forget the demo transcript from 2026-05-25T022914Z.md.** That's the gold artifact showing the agent caught its own setup bug. Should appear in the README + demo video.

# 23-day roadmap to submission

Tasks 011, 012, 013 have full specs in their respective files. Tasks 014–019 are outlined here and will be detail-spec'd as we approach them.

## Week 1 (Tier 1 — table-stakes)

### Task 011 — Wire xlsx + Merkle into aec_demo (0.5 day)
Closes the differentiator loop. Demo produces 4 files; `aec verify` works. **Fire immediately.**

### Task 012 — Live Splunk + BOTS v3 (2 days)
Real Splunk on the VM. One live control (SOC 2 CC6.1.c — MFA bypass). Regenerates the canned samples from live data.

### Task 013 — Splunk MCP integration (3 days)
Both `splunk/mcp-server-for-splunk` and `livehybrid/splunk-mcp` behind a runtime switch. **The headline hook.**

## Week 2 (Tier 2 — differentiators)

### Task 014 — Counter-evidence recurrence loop (2–3 days)
The Adversary persona's `recommended_additional_searches` get auto-executed (via MCP). Results feed back into a *second* panel round. Two-debate verdict has stronger weight. Demo beat: "the agent argues with itself, twice."

**Pre-spec sketch:**
- `panel.run_panel()` gains `recurrence: int = 0` kwarg (0 = current behavior; 1 = one extra round)
- After round 1's consensus, if any persona's `recommended_additional_searches` non-empty AND `AEC_RUN_ADVERSARY_SEARCHES=true`:
  - Execute each via MCP
  - Append results to the snapshot
  - Re-run the panel with the augmented snapshot
- Final verdict = lowest-of-three across BOTH rounds (most conservative wins)
- Transcript shows both rounds + a "what changed" summary
- ~300 LOC + tests

### Task 015 — Drift detection (2 days)
Same SPL, two time windows (e.g., this quarter vs last quarter), surface deltas. "MFA coverage dropped from 98% → 87%."

**Pre-spec sketch:**
- `aec_demo --compare T-90d:T-60d,T-30d:now` runs the same SPL twice
- New persona role (?): "Compliance trend analyst" OR add drift analysis to consensus
- Output: a "Compliance drift" section in the audit memo
- Probably needs synthetic drift data injection into BOTS v3 to make the demo land (real BOTS v3 data is static)
- ~250 LOC

### Task 016 — LangGraph wrapper + HITL gate (2 days)
Makes the "agentic" claim rigorous. Currently the pipeline is async function calls; LangGraph adds explicit state, checkpointing, and a human approval interrupt.

**Pre-spec sketch:**
- Wrap the existing aec_demo flow as a LangGraph graph (`src/aec/agent/graph.py`)
- 6 nodes: control_map → spl_gen → spl_validate → mcp_execute → normalize → panel → format
- HITL interrupt before `mcp_execute` (review SPL before running) and before `format` (review verdict before sealing)
- `aec_demo --review interactive` enables interrupts
- Default `--review auto` skips them (demo speed)
- ~400 LOC + tests
- State persists to `.aec_cache/graph_state_<run_id>.json`

## Week 3 (Tier 3 — moonshot)

### Task 017 — Differential framework mapping (2 days)
One prompt → satisfies multiple framework controls simultaneously. Leverages the 36-control catalog uniquely.

**Pre-spec sketch:**
- `aec_demo --control "SOC2:CC6.1+ISO:A.9.2.3+NIST:PR.AC-1"` (or natural language: "what evidence satisfies access control across all three frameworks?")
- Control mapper finds the *minimal* SPL set that covers all referenced controls
- The xlsx output has 3 evidence rows, one per framework, all sourced from the same SPL
- Demonstrates a multi-framework efficiency story
- ~200 LOC

### Task 018 — Splunk app package (`| auditcompiler`) (5 days)
The moonshot. Package as a custom Splunk search command, Splunkbase-deployable.

**Pre-spec sketch:**
- `splunk-app/auditcompiler/`
  - `default/app.conf`
  - `default/commands.conf` (register `| auditcompiler` as a custom command)
  - `bin/auditcompiler.py` (Splunk SDK ScriptingCommand)
- Inside `auditcompiler.py`: read the SPL row stream, invoke the panel, return enriched rows with `verdict`, `severity`, `root_cause` columns
- Demo: open Splunk Search bar, type `index=botsv3 sourcetype=o365 | auditcompiler control=CC6.1`, get back rows tagged with verdicts
- Packaged as `.spl` (Splunk tarball) per Splunkbase requirements
- Manifest / icon / app metadata for Splunkbase listing
- ~500 LOC + Splunk config files
- Highest integration risk; ship a stubbed version first that just echoes the SPL, then layer in real panel invocation

### Task 019 — Live web dashboard (3 days, optional)
Public URL where judges can see panel debates happening live.

**Pre-spec sketch:**
- FastAPI app at `web/main.py`
- Single page with a SPL input form
- Submits → server runs `aec_demo` programmatically → WebSocket streams the panel debate to the browser
- Same 3-column layout as the Rich TUI, in HTML
- Deployed to the Vultr VM under a subdomain (e.g., `aec.accessquint.com`)
- ~400 LOC + 200 LOC frontend (vanilla JS, no framework — keep it small)
- Stretch goal; do AFTER 018 is in any state

## Final 2 days (submission ceremony)

- Architecture diagram refresh (reflect MCP + LangGraph + panel + chain)
- README rewrite — "what's different" front-loaded
- 3-min demo video — script, takes, edit
- `docs/` polish: `auth-setup.md`, `splunk-setup.md`, `mcp-setup.md`, `architecture-decisions.md`
- Submission form on the hackathon portal
- Tag a release: `git tag v0.1.0-hackathon && git push --tags`

## Total LOC estimate

- 011: ~100
- 012: ~200
- 013: ~700
- 014: ~300
- 015: ~250
- 016: ~400
- 017: ~200
- 018: ~500 + Splunk conf
- 019: ~600
- **Total: ~3300 LOC** + tests + docs

Existing repo at task 010 merge: ~2300 LOC. Final at submission: ~5600 LOC. That's a substantial but not bloated open-source artifact.

## Tracking

This file is the source of truth. Update statuses here as tasks land:

- [ ] 011 — xlsx + Merkle wire
- [ ] 012 — Live Splunk + BOTS v3
- [ ] 013 — Splunk MCP integration
- [ ] 014 — Counter-evidence recurrence
- [ ] 015 — Drift detection
- [ ] 016 — LangGraph wrapper
- [ ] 017 — Differential framework mapping
- [ ] 018 — Splunk app package
- [ ] 019 — Web dashboard
- [ ] Architecture diagram refresh
- [ ] README rewrite
- [ ] Demo video
- [ ] Submission

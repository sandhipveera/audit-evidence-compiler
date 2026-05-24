# Task 005 — Demo CLI + scripted demo run

**Goal:** `aec ask --framework soc2 --control CC6.1 --output gap_report.xlsx` runs end-to-end in <30 seconds with terminal output suitable for screen-recording.

## Acceptance

- Rich-formatted progress (4 stages, each with a check or X).
- Final summary box showing: N controls checked, N evidence collected, N gaps, path to xlsx.
- `--demo` flag uses cached SPL results from `examples/cached_bots_results.json` so the demo never depends on Splunk being up at record time.
- Recorded narration script in `docs/demo-script.md` matches what the camera sees.

## Three demo controls (hardcoded for the recorded demo)

1. **SOC 2 CC6.1.a — Logical access provisioning** → SPL: `index=botsv3 sourcetype=wineventlog EventCode=4720 OR EventCode=4732 earliest=-30d | stats count by user`. Expected: returns rows → passed.
2. **SOC 2 CC6.1.b — Failed authentication monitoring** → SPL: `index=botsv3 sourcetype=wineventlog EventCode=4625 earliest=-30d | stats count by user host`. Expected: returns rows → passed.
3. **SOC 2 CC6.1.c — MFA enforcement** → SPL: `index=botsv3 sourcetype=o365:management:activity action=login NOT auth_type=mfa earliest=-30d | stats count by user`. Expected: returns N>0 rows → **gap finding** (drops into xlsx as Critical severity, root_cause="MFA not enforced for N users").

## Files to create

- Wire `src/aec/cli.py` to call `aec.agent.graph.run()`
- `docs/demo-script.md` — 3-minute narration with timestamps + camera cues
- `examples/cached_bots_results.json` — recorded SPL output for offline demo
- `examples/sample_output.xlsx` — golden xlsx for README screenshot

## Out of scope

- Web UI (terminal is the demo)
- Multi-framework batch mode
- Scheduled / cron-driven runs

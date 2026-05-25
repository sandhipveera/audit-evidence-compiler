# Task 018 — Splunk app package: `| auditcompiler` custom search command

**Goal:** Package the audit pipeline as a native Splunk app deployable to Splunkbase. Inside Splunk's search bar, users type `index=botsv3 sourcetype=o365:management:activity | auditcompiler control=CC6.1`. The custom search command pipes the row stream into our agent, runs the panel debate, and returns enriched rows with `verdict`, `severity`, `root_cause`, `recommended_actions` columns.

**Budget:** ~500 LOC + Splunk conf files. ~5 days of YC time. The moonshot.

## Why this is the moonshot

Every other hackathon entry will *call* Splunk from the outside. We're the only one that *lives inside* Splunk's query pipeline. Demo beat: open Splunk Web, type SPL with `| auditcompiler` in it, watch the panel debate run, see the enriched results show up *in Splunk's table view*. That's a category of integration nobody else will have.

Splunkbase-deployable means judges (or anyone) can install it: `Settings → Apps → Find More Apps → "Audit Evidence Compiler"`.

## Architecture

```
Splunk Search:
  index=botsv3 sourcetype=o365:management:activity | auditcompiler control=CC6.1

Splunk dispatches to:
  splunk-app/bin/auditcompiler.py  (a ScriptingCommand subclass)

Inside auditcompiler.py:
  1. Read row stream from Splunk (via Splunk SDK ChunkedExternalCommand protocol)
  2. Aggregate rows into a snapshot dict (same shape our agent already expects)
  3. Invoke aec.agent.panel.run_panel(snapshot, control_id=arg.control)
  4. Map PanelResult → row-level enrichment:
     - Each input row gets verdict / severity / root_cause appended
     - OR: emit a single summary row with consensus + counter-searches
  5. Stream enriched rows back to Splunk

Splunk renders the result as a regular search results table.
```

## Splunk app structure

```
splunk-app/auditcompiler/
├── default/
│   ├── app.conf                    # App metadata (Splunkbase manifest)
│   ├── commands.conf               # Register `| auditcompiler` as a search command
│   ├── inputs.conf                 # (optional) for scheduled saved searches
│   └── README.md
├── bin/
│   ├── auditcompiler.py            # The custom command implementation
│   └── lib/                        # Vendored dependencies (Splunk apps can't pip install)
│       ├── aec/                    # Symlink or copy of src/aec/
│       ├── anthropic/
│       ├── openai/
│       └── ...
├── metadata/
│   └── default.meta                # Permissions (global)
├── static/
│   ├── appIcon.png                 # App icon for Splunkbase
│   └── appIcon_2x.png
└── package.sh                      # Builds the .spl tarball
```

## The custom command (auditcompiler.py)

Splunk's Python SDK provides `splunklib.searchcommands.StreamingCommand` and `EventingCommand`. Pick `EventingCommand` so we receive the full row stream + can return a different number of rows.

```python
from splunklib.searchcommands import (
    dispatch, EventingCommand, Configuration, Option, validators
)

@Configuration()
class AuditCompilerCommand(EventingCommand):
    control = Option(require=True, doc='Framework control ID (e.g., CC6.1)')
    framework = Option(default='SOC2', doc='Framework: SOC2|ISO|NIST-CSF')
    transport = Option(default='rest', doc='LLM transport: cli|api')
    mode = Option(default='enrich', doc='enrich (per-row) | summary (single row)')

    def transform(self, events):
        rows = list(events)
        snapshot = self._build_snapshot(rows)
        result = asyncio.run(run_panel(snapshot, self.control))

        if self.mode == 'summary':
            yield {
                'consensus': result.final_verdict,
                'auditor_verdict': result.critiques[0].verdict,
                'engineer_verdict': result.critiques[1].verdict,
                'adversary_verdict': result.critiques[2].verdict,
                'root_cause': result.consensus_rationale,
                'event_count': len(rows),
            }
        else:  # enrich
            for row in rows:
                row['verdict'] = result.final_verdict
                row['severity'] = self._severity(result.final_verdict)
                row['root_cause'] = result.consensus_rationale
                yield row

dispatch(AuditCompilerCommand, sys.argv, sys.stdin, sys.stdout, __name__)
```

## Vendoring dependencies

Splunk apps can't run `pip install`. We need to vendor everything `aec` imports + the LLM SDKs into `bin/lib/`. Use `pip install -t bin/lib/ ...` + the package.sh script.

Vendoring gotchas:
- `anthropic` SDK pulls in `httpx`, `pydantic`, `typing-extensions` — all need to land
- Python version inside Splunk is 3.7 (Splunk 9) or 3.9 (Splunk 10). Check compatibility for `pydantic>=2`. If incompatible, downgrade or shim.
- Total vendored size budget: <100MB (Splunkbase enforces)

## Package + distribute

`package.sh`:
```bash
#!/usr/bin/env bash
set -e
APP_DIR="splunk-app/auditcompiler"
OUT="dist/auditcompiler-$(date +%Y%m%d).spl"

# Strip __pycache__, .pyc, tests
find "$APP_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$APP_DIR" -name "*.pyc" -delete

mkdir -p dist
tar -czf "$OUT" -C splunk-app auditcompiler
echo "Built: $OUT"
```

Install:
```bash
# Local install for testing
docker cp dist/auditcompiler-<date>.spl splunk:/tmp/
docker exec splunk /opt/splunk/bin/splunk install app /tmp/auditcompiler-<date>.spl
docker exec splunk /opt/splunk/bin/splunk restart
```

## Demo SPL

```spl
| inputlookup admin_logins.csv
| auditcompiler control=CC6.1 framework=SOC2 mode=enrich
| table _time user verdict severity root_cause
```

Or full pipeline:
```spl
index=botsv3 sourcetype=o365:management:activity action=Login
| stats count by user, mfa_used
| auditcompiler control=CC6.1 mode=summary
```

## Files to create

- `splunk-app/auditcompiler/default/app.conf`
- `splunk-app/auditcompiler/default/commands.conf`
- `splunk-app/auditcompiler/default/README.md` (shown in Splunkbase listing)
- `splunk-app/auditcompiler/bin/auditcompiler.py` (~200 LOC)
- `splunk-app/auditcompiler/metadata/default.meta`
- `splunk-app/auditcompiler/static/appIcon.png` (256x256, simple — text logo over a shield)
- `splunk-app/auditcompiler/static/appIcon_2x.png` (512x512)
- `package.sh` at repo root
- `docs/splunk-app-install.md` — install instructions
- `tests/test_splunk_app.py` (~80 LOC) — covers:
  - `auditcompiler.py` invoked with mock argv produces expected stdout chunks
  - `mode=enrich` returns all input rows + verdict column
  - `mode=summary` returns 1 row per invocation
  - Missing required `control` argument raises clear error
  - Vendored imports load correctly from `bin/lib/`

## Constraints

- LOC budget 500 + Splunk conf files
- Vendored package size < 100MB (Splunkbase requirement)
- Compatible with Splunk Enterprise 9.x and 10.x
- No external network calls *from* Splunk to anywhere except LLM providers (judges may test on air-gapped instances; document the LLM API dependency clearly in the README)
- Local development must work via `docker exec splunk /opt/splunk/bin/splunk install app /tmp/auditcompiler.spl`

## Risk mitigation (ship in stages)

This task has the highest integration risk. Ship in three checkpoints — each is independently mergeable:

**Stage A — Stub command (1 day):**
- App structure with `auditcompiler.py` that ignores input and emits a hardcoded "Hello from auditcompiler" row
- Confirms Splunk app loading + custom command registration works
- Merge even if rest isn't ready

**Stage B — Real invocation, hardcoded model (2 days):**
- `auditcompiler.py` calls panel with hardcoded SOC 2 CC6.1 + a single fixture snapshot
- Returns real verdict row
- Confirms vendored deps work inside Splunk's Python

**Stage C — Full feature (2 days):**
- `control`, `framework`, `transport`, `mode` arguments wired through
- Real snapshot built from input row stream
- Documented for Splunkbase submission

## Definition of done

- `package.sh` produces an installable `.spl`
- `docker exec splunk /opt/splunk/bin/splunk install app ...` succeeds
- Splunk search `| auditcompiler control=CC6.1` returns real panel verdicts
- README explains LLM API key requirement + how to set it in Splunk env
- All three stages merge cleanly; stages A and B work independently if C runs into Python-version issues

## Demo cue (the cinematic shot — 30 seconds)

Open Splunk Web, type the SPL on screen, hit search, watch the result populate with verdict + root_cause columns. Voice-over:

*"Every other entry calls Splunk from the outside. This one is a search command inside Splunk's pipeline. You type `auditcompiler` in any search; three vendor AI models debate the evidence; results stream back as columns in your Splunk table."*

Then a moment of silence so the auditor-grade enrichment columns sink in.

## Out of scope

- Splunk Cloud installation (Splunkbase manifest covers it; tested install only on Enterprise)
- Scheduled / saved search auto-invocation
- A custom UI inside Splunk (just commands.conf — no Splunk app pages)
- Multi-tenant license-aware execution caps

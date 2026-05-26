# Task 022 — Compliance Posture Dashboard (Splunk Dashboard Studio)

**Goal:** A real Splunk Dashboard Studio dashboard that shows live PASS/FAIL/DRIFT status across all 36 controls, updated via scheduled `| auditcompiler` saved searches. Turns the CLI tool into a product. The dashboard ships as part of the Splunk app package.

**Budget:** ~150 LOC (JSON dashboard + Python helper) + Splunk conf. ~2 days.

## Why this completes the story

Right now the output is artifacts in a directory. Judges see a terminal. Adding a Splunk dashboard means:
- Compliance posture is visible inside Splunk, where security teams already live
- The `| auditcompiler` command becomes genuinely useful (scheduled, always-on)
- Demo beat: open Splunk Web, see a live dashboard showing 36 controls, each with a color-coded verdict badge

## Architecture

```
Scheduled Splunk saved searches (every 4h):
  "CC6.1 — MFA Enforcement": | auditcompiler control=CC6.1 mode=summary
  "CC7.2 — Incident Response": | auditcompiler control=CC7.2 mode=summary
  ... (one per control)

Results saved to: index=auditcompiler_posture

Dashboard Studio panels query index=auditcompiler_posture:
  - Scoreboard: 36 colored tiles (PASS=green, FAIL=red, INSUFFICIENT=purple, PARTIAL=yellow)
  - Trend chart: verdict changes over the past 30 days
  - Recent findings table: latest control + verdict + timestamp
  - Framework coverage: SOC2 / ISO / NIST / COBIT completion rings
```

## Dashboard JSON structure

`splunk-app/auditcompiler/default/data/ui/dashboards/posture.json`:

```json
{
  "visualizations": {
    "posture_scorecard": {
      "type": "splunk.singlevalue",
      "dataSources": { "primary": "posture_search" },
      "title": "Controls Passing",
      "options": { "majorColor": "> majorValue | rangeValue(colorRanges)" }
    },
    "verdict_tiles": {
      "type": "splunk.table",
      "dataSources": { "primary": "verdicts_by_control" },
      "options": {
        "columnFormat": {
          "verdict": {
            "data": "> table | seriesByName('verdict')",
            "render": "sparkline"
          }
        }
      }
    },
    "trend_chart": {
      "type": "splunk.line",
      "dataSources": { "primary": "trend_search" },
      "title": "Compliance Posture Trend (30d)"
    }
  },
  "dataSources": {
    "verdicts_by_control": {
      "type": "ds.search",
      "options": {
        "query": "index=auditcompiler_posture | stats latest(verdict) as verdict, latest(_time) as last_run by control_id framework | sort -last_run"
      }
    },
    "trend_search": {
      "type": "ds.search",
      "options": {
        "query": "index=auditcompiler_posture | timechart span=1d count by verdict"
      }
    }
  }
}
```

## Scheduled saved searches conf

`splunk-app/auditcompiler/default/savedsearches.conf`:
```ini
[AEC - CC6.1 MFA Enforcement]
search = | auditcompiler control=CC6.1 mode=summary | eval control_id="CC6.1", framework="SOC2" | collect index=auditcompiler_posture
cron_schedule = 0 */4 * * *
enableSched = 1
dispatch.earliest_time = 2018-08-01
dispatch.latest_time = 2018-09-30
alert.track = 0

[AEC - CC7.2 Incident Response]
search = | auditcompiler control=CC7.2 mode=summary | eval control_id="CC7.2", framework="SOC2" | collect index=auditcompiler_posture
cron_schedule = 30 */4 * * *
enableSched = 1
```

(Generate one stanza per control from the 36-control catalog.)

## Python helper: generate_savedsearches.py

Since we have 36 controls, generate the conf programmatically:

```python
# scripts/generate_savedsearches.py
import json
from pathlib import Path

catalog = json.loads(Path("src/aec/priors/catalog.json").read_text())
conf_lines = []
for i, ctrl in enumerate(catalog["controls"]):
    offset_min = (i * 5) % 60  # stagger start times
    conf_lines.append(f"""
[AEC - {ctrl['id']} {ctrl['name']}]
search = | auditcompiler control={ctrl['framework_controls']['soc2'] or ctrl['id']} mode=summary | eval control_id="{ctrl['id']}", framework="{list(ctrl['framework_controls'].keys())[0]}" | collect index=auditcompiler_posture
cron_schedule = {offset_min} */4 * * *
enableSched = 1
""")
Path("splunk-app/auditcompiler/default/savedsearches.conf").write_text("\n".join(conf_lines))
```

## Index definition

`splunk-app/auditcompiler/default/indexes.conf`:
```ini
[auditcompiler_posture]
homePath = $SPLUNK_DB/auditcompiler_posture/db
coldPath = $SPLUNK_DB/auditcompiler_posture/colddb
thawedPath = $SPLUNK_DB/auditcompiler_posture/thaweddb
```

## Files to create / modify

- `splunk-app/auditcompiler/default/data/ui/dashboards/posture.json` (Dashboard Studio JSON, ~80 LOC)
- `splunk-app/auditcompiler/default/savedsearches.conf` (generated from catalog)
- `splunk-app/auditcompiler/default/indexes.conf`
- `splunk-app/auditcompiler/default/data/ui/nav/default.xml` — add Dashboard to app nav
- `scripts/generate_savedsearches.py` (~40 LOC)
- `docs/splunk-app-install.md` — add dashboard + scheduled search section

## Definition of done

- Dashboard appears in Splunk app nav under "Compliance Posture"
- Scorecard, verdict table, and trend chart render (even with empty data on first install)
- `savedsearches.conf` has one stanza per control in `catalog.json`
- `| collect index=auditcompiler_posture` populates data after a `| auditcompiler` run
- Dashboard shows real verdicts after first scheduled run

## Demo cue (20 seconds)

Open Splunk Web → Audit Evidence Compiler app → Compliance Posture dashboard. 36 control tiles visible. Zoom in on CC6.1 tile showing FAIL in red. Trend chart shows it flipped from PASS to FAIL 3 days ago (the drift detection story).

Voice-over: "This is compliance posture, always-on, inside Splunk. Every four hours, the agent re-runs the evidence collection. When a control flips from PASS to FAIL, you see it here — before the auditor does."

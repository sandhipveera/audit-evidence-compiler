# Task 015 — Drift detection (two-window compliance comparison)

**Goal:** Run the same SPL against two time windows and surface deltas. "MFA coverage dropped from 98% → 87% over the last 90 days" is the continuous-compliance story most audit tools miss.

**Budget:** ~250 LOC + tests. ~2 days of YC time.

## Behavior spec

1. `aec_demo --compare T1:T2` runs the snapshot twice with different time ranges.
2. Snapshot adapter computes a `Drift` record summarizing:
   - Per-aggregation delta (absolute + percentage)
   - Direction (improving / stable / worsening)
   - Material-change flag (delta > configurable threshold, default 5%)
3. New persona-injection: each persona prompt receives both snapshots + the drift summary, and is asked to evaluate compliance *trend*, not just current state.
4. Drift block appears in the transcript and the audit memo.

## CLI surface

```
aec_demo --control CC6.1 --compare "2018-08-01:2018-08-15,2018-09-01:2018-09-15"
aec_demo --control CC6.1 --drift-window 90d   # shorthand: compare last 90d vs the 90d before that
```

If no `--compare` flag: runs single-window as today, no drift analysis.

## Why BOTS v3 needs synthetic data

BOTS v3 is static. To make drift visible in the demo, we need two distinguishable time windows *within* the BOTS data. Two approaches:

**A. Use BOTS's actual data eras** — BOTS v3 has events from 2018-08 to 2018-09; pick two non-overlapping 1-week windows within it. Real drift will be small/random because attacks in the dataset are clustered, but the *infrastructure works* — we can show drift detection mechanically.

**B. Inject synthetic drift** — generate a "follow-up" sample (`samples/soc2-cc61-q2.json`) with the same SPL but altered aggregations (e.g., `mfa_enforced_pct: 0.83 → 0.71`). Demo runs `--compare sample1,sample2` against canned data. More cinematic but less "live."

Pick A as the default for `--live` mode, B as the `--sample` mode. Both ship.

## Models

```python
class DriftMetric(BaseModel):
    name: str                # e.g., "mfa_enforced_pct"
    value_1: float | int
    value_2: float | int
    delta_abs: float
    delta_pct: float
    direction: Literal["improving", "stable", "worsening"]
    material: bool           # delta_pct > threshold

class DriftAnalysis(BaseModel):
    window_1: dict           # {"earliest": "...", "latest": "..."}
    window_2: dict
    metrics: list[DriftMetric]
    overall_direction: Literal["improving", "stable", "worsening"]
    summary: str             # LLM-drafted one-liner: "MFA coverage dropped 12% over 90d"

class TwoWindowSnapshot(BaseModel):
    control_id: str
    snapshot_1: dict
    snapshot_2: dict
    drift: DriftAnalysis
```

## Transcript format addition

```markdown
## Drift analysis

Window 1: 2018-08-01 to 2018-08-15  (mfa_enforced=98%, n=1247)
Window 2: 2018-09-01 to 2018-09-15  (mfa_enforced=87%, n=1389)

| Metric             | Window 1 | Window 2 | Δ      | Direction  | Material |
|--------------------|----------|----------|--------|------------|----------|
| mfa_enforced_pct   | 0.98     | 0.87     | -11.2% | worsening  | ✓        |
| failed_login_count | 49       | 128      | +161%  | worsening  | ✓        |
| total_logins       | 1247     | 1389     | +11.4% | stable     |          |

Overall: WORSENING — material regressions in 2 of 3 metrics.

## Panel debate (drift-aware)

The personas evaluated both windows + the drift summary.

### Auditor
- Verdict: FAIL
- Reasoning: 11% drop in MFA enforcement over 30 days, combined with 3x rise in
  failed logins, indicates active control degradation. CC6.1 requires not just
  point-in-time evidence but ongoing operating effectiveness.
...
```

## Persona prompt updates

`src/aec/agent/personas/*.md` gain an optional appendix injected when `drift` is present:

```
You are also evaluating compliance TREND, not just current state. The two
snapshots below cover {window_1} and {window_2}. Material drift (>5% delta)
indicates a control may be degrading even if today's snapshot looks healthy.

When drift.overall_direction is "worsening", consider verdicts more
conservatively — a passing point-in-time check with worsening drift is
weaker evidence than a stable trend.

Drift summary:
{drift.summary}

Per-metric:
{drift.metrics formatted as a table}
```

This is appended to the existing persona system prompt; not a replacement.

## Files to create / modify

- `src/aec/splunk/drift.py` — `compute_drift(snapshot_1, snapshot_2) -> DriftAnalysis` (~100 LOC)
- `src/aec/agent/models.py` — `DriftMetric`, `DriftAnalysis`, `TwoWindowSnapshot`
- `src/aec/agent/panel.py` — `run_panel()` accepts optional `drift: DriftAnalysis` kwarg, injects into persona prompts
- `cli/aec_demo.py` — `--compare` and `--drift-window` flags
- `samples/soc2-cc61-q2.json` — synthetic follow-up snapshot for offline demo (~30 LOC of JSON)
- `tests/test_drift.py` (~100 LOC) — drift computation, threshold tuning, direction logic
- `tests/test_drift_panel.py` (~80 LOC) — panel debate with drift context produces drift-aware verdicts

## Definition of done

- `aec_demo --control CC6.1 --compare "...,..."` runs end-to-end
- `aec_demo --control CC6.1 --drift-window 30d` works against live Splunk
- Transcript has a "Drift analysis" block with the metrics table
- Audit memo's executive summary mentions trend if drift is worsening
- Tests cover threshold edge cases (5% exactly = not material, 5.1% = material)
- Two sample files (`samples/soc2-cc61.json` + `samples/soc2-cc61-q2.json`) for offline demo

## Out of scope

- More than two windows
- Statistical significance testing (a 5% threshold is enough for the demo)
- Forecasting future drift
- Alerting (drift detection is read-only)

## Demo cue (10 seconds)

```
$ aec_demo --control CC6.1 --drift-window 30d

[1/6] Fetching window 1 snapshot... (Aug 2018)
[2/6] Fetching window 2 snapshot... (Sep 2018)
[3/6] Drift analysis: MFA coverage dropped 11.2% over 30 days
[4/6] Generating SPL for follow-up evidence
[5/6] Panel debate (drift-aware)...
[6/6] Consensus: FAIL — material regression in MFA + auth failure metrics

Wrote out/audit_memo_<ts>.md (drift summary in executive summary)
```

This is the "continuous compliance" story most audit tools never deliver.

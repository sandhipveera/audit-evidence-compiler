"""Two-window drift detection — compares aggregation snapshots across time windows."""
from __future__ import annotations

from typing import Any, Literal

from aec.agent.models import DriftAnalysis, DriftMetric

IMPROVING_METRICS = {"mfa_enforced_pct"}
WORSENING_METRICS = {"failed_logins", "failed_login_count", "service_accounts_bypassing_mfa"}

DEFAULT_THRESHOLD_PCT = 5.0


def _classify_direction(
    name: str,
    delta_pct: float,
    threshold: float,
) -> Literal["improving", "stable", "worsening"]:
    if abs(delta_pct) <= threshold:
        return "stable"

    if name in IMPROVING_METRICS:
        return "improving" if delta_pct > 0 else "worsening"
    if name in WORSENING_METRICS:
        return "worsening" if delta_pct > 0 else "improving"

    return "stable"


def compute_drift(
    snapshot_1: dict[str, Any],
    snapshot_2: dict[str, Any],
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
) -> DriftAnalysis:
    """Compare aggregations from two snapshots and produce a DriftAnalysis.

    snapshot_1 is the earlier window, snapshot_2 is the later window.
    A metric is "material" when its absolute percentage delta exceeds the threshold
    (strictly greater than, not equal to).
    """
    agg_1 = snapshot_1.get("aggregations", {})
    agg_2 = snapshot_2.get("aggregations", {})

    all_keys = sorted(set(agg_1.keys()) | set(agg_2.keys()))
    numeric_keys = [
        k for k in all_keys
        if isinstance(agg_1.get(k), (int, float)) and isinstance(agg_2.get(k), (int, float))
    ]

    metrics: list[DriftMetric] = []
    for key in numeric_keys:
        v1 = agg_1[key]
        v2 = agg_2[key]
        delta_abs = float(v2) - float(v1)

        if float(v1) != 0.0:
            delta_pct = (delta_abs / abs(float(v1))) * 100.0
        elif delta_abs != 0.0:
            delta_pct = 100.0 if delta_abs > 0 else -100.0
        else:
            delta_pct = 0.0

        direction = _classify_direction(key, delta_pct, threshold_pct)
        material = abs(delta_pct) > threshold_pct

        metrics.append(DriftMetric(
            name=key,
            value_1=v1,
            value_2=v2,
            delta_abs=round(delta_abs, 4),
            delta_pct=round(delta_pct, 2),
            direction=direction,
            material=material,
        ))

    overall = _compute_overall_direction(metrics)
    summary = _build_summary(metrics, snapshot_1, snapshot_2)

    window_1 = _extract_window(snapshot_1)
    window_2 = _extract_window(snapshot_2)

    return DriftAnalysis(
        window_1=window_1,
        window_2=window_2,
        metrics=metrics,
        overall_direction=overall,
        summary=summary,
    )


def _extract_window(snapshot: dict[str, Any]) -> dict[str, str]:
    tr = snapshot.get("time_range", {})
    return {
        "earliest": str(tr.get("earliest", "")),
        "latest": str(tr.get("latest", "")),
    }


def _compute_overall_direction(
    metrics: list[DriftMetric],
) -> Literal["improving", "stable", "worsening"]:
    material = [m for m in metrics if m.material]
    if not material:
        return "stable"

    worsening = sum(1 for m in material if m.direction == "worsening")
    improving = sum(1 for m in material if m.direction == "improving")

    if worsening > improving:
        return "worsening"
    if improving > worsening:
        return "improving"
    return "stable"


def _build_summary(
    metrics: list[DriftMetric],
    snapshot_1: dict[str, Any],
    snapshot_2: dict[str, Any],
) -> str:
    material = [m for m in metrics if m.material]
    if not material:
        return "No material drift detected between the two windows."

    parts = []
    for m in material:
        sign = "+" if m.delta_pct > 0 else ""
        parts.append(f"{m.name} {sign}{m.delta_pct:.1f}%")

    return f"Material changes: {'; '.join(parts)}."


def format_drift_transcript(drift: DriftAnalysis) -> str:
    """Render the drift analysis as a markdown block for the transcript."""
    lines = [
        "## Drift analysis",
        "",
        f"Window 1: {drift.window_1.get('earliest', '?')} to {drift.window_1.get('latest', '?')}",
        f"Window 2: {drift.window_2.get('earliest', '?')} to {drift.window_2.get('latest', '?')}",
        "",
        "| Metric | Window 1 | Window 2 | Δ | Direction | Material |",
        "|--------|----------|----------|---|-----------|----------|",
    ]

    for m in drift.metrics:
        v1 = f"{m.value_1:.2f}" if isinstance(m.value_1, float) else str(m.value_1)
        v2 = f"{m.value_2:.2f}" if isinstance(m.value_2, float) else str(m.value_2)
        sign = "+" if m.delta_pct > 0 else ""
        mat = "✓" if m.material else ""
        lines.append(
            f"| {m.name} | {v1} | {v2} | {sign}{m.delta_pct:.1f}% | {m.direction} | {mat} |"
        )

    lines.append("")
    material_count = sum(1 for m in drift.metrics if m.material)
    total_count = len(drift.metrics)
    lines.append(
        f"Overall: {drift.overall_direction.upper()} — "
        f"material changes in {material_count} of {total_count} metrics."
    )
    lines.append("")

    return "\n".join(lines)


def format_drift_persona_appendix(drift: DriftAnalysis) -> str:
    """Build the appendix injected into persona system prompts when drift is present."""
    table_lines = []
    for m in drift.metrics:
        sign = "+" if m.delta_pct > 0 else ""
        mat = "YES" if m.material else "no"
        table_lines.append(f"  {m.name}: {m.value_1} -> {m.value_2} ({sign}{m.delta_pct:.1f}%, {m.direction}, material={mat})")

    metrics_table = "\n".join(table_lines)

    return (
        "\n\n---\n\n"
        "You are also evaluating compliance TREND, not just current state. The two "
        f"snapshots below cover window 1 ({drift.window_1.get('earliest', '?')} to "
        f"{drift.window_1.get('latest', '?')}) and window 2 ({drift.window_2.get('earliest', '?')} "
        f"to {drift.window_2.get('latest', '?')}). Material drift (>5% delta) "
        "indicates a control may be degrading even if today's snapshot looks healthy.\n\n"
        f"When drift.overall_direction is \"{drift.overall_direction}\", consider verdicts more "
        "conservatively — a passing point-in-time check with worsening drift is "
        "weaker evidence than a stable trend.\n\n"
        f"Drift summary: {drift.summary}\n\n"
        f"Per-metric:\n{metrics_table}"
    )

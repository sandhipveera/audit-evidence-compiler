"""Audit Findings xlsx formatter — writes GapFinding rows into the branded template.

Usage:
    python -m aec.formatter.audit_findings --in evidence.json --out gap_report.xlsx
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

import openpyxl
from pydantic import BaseModel


TEMPLATE_RESOURCE = "aec.formatter.templates"
TEMPLATE_FILENAME = "audit_findings_blank.xlsx"
SHEET_NAME = "Audit Remediation"
DATA_START_ROW = 9
DATA_START_COL = 2  # column B

SEVERITY_SCORES: dict[str, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

CLOSURE_DAYS: dict[str, int] = {
    "low": 365,
    "medium": 180,
    "high": 90,
    "critical": 90,
}


class GapFinding(BaseModel):
    finding_id: str
    audit_type: str = "Internal"
    framework: str = ""
    audit_reference: str = ""
    finding_description: str = ""
    finding_category: str = ""
    severity: str = "Medium"
    root_cause: str = ""
    affected_system: str = ""
    risk_owner: str = ""
    remediation_action: str = ""
    remediation_owner: str = ""
    current_status: str = "Open"
    evidence_reference: str = ""
    comments: str = ""

    @property
    def severity_score(self) -> int:
        return SEVERITY_SCORES.get(self.severity.lower(), 2)

    def target_closure_date(self, reference_date: date | None = None) -> date:
        ref = reference_date or date.today()
        days = CLOSURE_DAYS.get(self.severity.lower(), 180)
        return ref + timedelta(days=days)


def _get_template_path() -> Path:
    return Path(str(files(TEMPLATE_RESOURCE) / TEMPLATE_FILENAME))


def _finding_to_row(finding: GapFinding, reference_date: date | None = None) -> list[Any]:
    """Convert a GapFinding to a list of cell values matching columns B-U."""
    return [
        finding.finding_id,                              # B: Finding ID
        finding.audit_type,                              # C: Audit Type
        finding.framework,                               # D: Framework
        finding.audit_reference,                         # E: Audit Reference
        finding.finding_description,                     # F: Finding Description
        finding.finding_category,                        # G: Finding Category
        finding.severity,                                # H: Severity
        finding.severity_score,                          # I: Severity Score
        finding.root_cause,                              # J: Root Cause
        finding.affected_system,                         # K: Affected System / Process
        finding.risk_owner,                              # L: Risk Owner
        finding.remediation_action,                      # M: Remediation Action
        finding.remediation_owner,                       # N: Remediation Owner
        finding.target_closure_date(reference_date),     # O: Target Closure Date
        finding.current_status,                          # P: Current Status
        None,                                            # Q: Closure Date
        None,                                            # R: Residual Risk Level
        finding.evidence_reference,                      # S: Evidence Reference
        None,                                            # T: Last Review Date
        finding.comments,                                # U: Comments
    ]


def write_findings(
    findings: list[GapFinding],
    output_path: Path,
    *,
    reference_date: date | None = None,
) -> Path:
    """Copy the blank template to *output_path* and populate it with *findings*.

    Returns the output path for convenience.
    """
    template = _get_template_path()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, output_path)

    wb = openpyxl.load_workbook(output_path)
    ws = wb[SHEET_NAME]

    for row_offset, finding in enumerate(findings):
        row_values = _finding_to_row(finding, reference_date)
        row_num = DATA_START_ROW + row_offset
        for col_offset, value in enumerate(row_values):
            if value is not None:
                ws.cell(row=row_num, column=DATA_START_COL + col_offset, value=value)

    wb.save(output_path)
    return output_path


def load_findings_from_json(path: Path) -> list[GapFinding]:
    """Load gap findings from a JSON file (list of objects)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "findings" in raw:
        raw = raw["findings"]
    return [GapFinding.model_validate(item) for item in raw]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Write audit gap findings into the branded xlsx template.",
    )
    ap.add_argument("--in", dest="input", required=True, help="Path to evidence JSON file")
    ap.add_argument("--out", default="gap_report.xlsx", help="Output xlsx path")
    args = ap.parse_args()

    findings = load_findings_from_json(Path(args.input))
    out = write_findings(findings, Path(args.out))
    print(f"[ok] {len(findings)} findings → {out}")


if __name__ == "__main__":
    main()

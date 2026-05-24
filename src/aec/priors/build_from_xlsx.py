"""
One-shot parser: read AccessQuint vCISO core-biz xlsx templates,
extract a generic control catalog as JSON priors.

The raw xlsx files are NOT committed to this repo. This script reads from
a local path provided via --source and writes catalog.json + a sanitized
blank xlsx template.

Usage:
    python -m aec.priors.build_from_xlsx \
        --source "/Users/admin/Documents/AI Projects/accessquint/core-biz" \
        --out src/aec/priors/catalog.json \
        --template-out src/aec/formatter/templates/audit_findings_blank.xlsx
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
from copy import copy
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl import load_workbook


FRAMEWORKS = ["ISO 27001", "NIST 800-53", "NIST CSF", "SOC 2", "COBIT"]


def _norm(v: Any) -> str:
    return "" if v is None else str(v).strip()


def parse_control_framework_mapping(path: Path) -> list[dict]:
    """Control Framework Mapping.xlsx → list of controls with framework membership."""
    wb = load_workbook(path, data_only=True)
    ws = wb["Sheet1"]
    controls = []
    current_category = None
    # header is row 8, data starts row 9; rows 9, 22, etc. are category dividers
    for row in ws.iter_rows(min_row=9, values_only=True):
        # offset by 1 column (data starts at column B)
        cells = [_norm(c) for c in row[1:12]]
        if not any(cells):
            continue
        control_id, name, category, owner, status, effectiveness, iso, nist, soc2, cobit, score = cells
        # category-divider rows have only column 1 populated
        if control_id and not name:
            current_category = control_id
            continue
        if not control_id.startswith("CTRL"):
            continue
        controls.append({
            "internal_id": control_id,
            "name": name,
            "category": current_category or category,  # Preventive / Detective / Corrective
            "control_family": name,  # e.g., "Patch Management", "Access Control Policy"
            "frameworks": {
                "ISO 27001": iso.lower() == "y",
                "NIST 800-53": nist.lower() == "y",
                "SOC 2": soc2.lower() == "y",
                "COBIT": cobit.lower() == "y",
            },
        })
    return controls


def parse_control_mapping_matrix(path: Path) -> list[dict]:
    """Control Mapping Matrix.xlsx → per-framework control rows."""
    wb = load_workbook(path, data_only=True)
    ws = wb["Control Mapping Matrix"]
    rows = []
    for row in ws.iter_rows(min_row=9, values_only=True):
        cells = [_norm(c) for c in row[1:11]]
        if not any(cells) or not cells[0].isdigit():
            continue
        mapping_id, framework, fwk_ctrl_id, internal_id, category, mapped, strength, _owner, status, impact = cells
        rows.append({
            "framework": framework,
            "framework_control_id": fwk_ctrl_id,
            "internal_id": internal_id,
            "category": category,  # Risk Mgmt / Access Control / Data Protection / Incident Response
            "mapped": mapped.lower() == "yes",
            "strength": strength,
            "implementation_status": status,
            "compliance_impact": impact,
        })
    return rows


# Hand-curated SPL evidence hints: how each control category translates to a
# Splunk search pattern. These are the "vCISO judgement calls" baked into the prior.
SPL_HINTS = {
    "Access Control": {
        "splunk_indicators": ["login_failed", "privilege_grant", "user_created", "mfa_used"],
        "spl_skeleton": 'index=* (action=login OR action=privilege_grant OR action=user_created) earliest=-90d | stats count by user action',
        "evidence_question": "Who can access what, how is access provisioned/reviewed, and is MFA enforced?",
    },
    "Logging & Monitoring": {
        "splunk_indicators": ["index health", "ingestion lag", "data sources reporting"],
        "spl_skeleton": '| metadata type=sourcetypes index=* | eval lag_min=(now()-recentTime)/60 | where lag_min > 60',
        "evidence_question": "Are security-relevant log sources ingested continuously and monitored for failure?",
    },
    "Incident Response": {
        "splunk_indicators": ["notable events", "incident_id", "MTTD", "MTTC"],
        "spl_skeleton": 'index=notable earliest=-90d | stats count earliest(_time) latest(_time) by incident_id severity',
        "evidence_question": "Are incidents detected, triaged, contained within SLA, and post-mortemed?",
    },
    "Data Protection": {
        "splunk_indicators": ["encryption_status", "dlp_event", "data_exfil"],
        "spl_skeleton": 'index=* sourcetype=dlp OR sourcetype=*encryption* earliest=-90d | stats count by host action',
        "evidence_question": "Is sensitive data encrypted in transit/at rest, and is data exfiltration detected?",
    },
    "Risk Management": {
        "splunk_indicators": ["risk_score", "asset_criticality", "vuln_severity"],
        "spl_skeleton": '| inputlookup asset_inventory | join host [search index=vuln earliest=-30d] | stats max(severity) by host criticality',
        "evidence_question": "Are risks identified, scored, and tracked through to remediation?",
    },
    "Vendor Risk": {
        "splunk_indicators": ["third_party_access", "vendor_login"],
        "spl_skeleton": 'index=* (tag=authentication user IN (*vendor*, *contractor*)) earliest=-90d | stats count by user src_ip',
        "evidence_question": "Is third-party access scoped, logged, and reviewed?",
    },
    "Policy": {
        "splunk_indicators": ["policy_acknowledged", "training_completed"],
        "spl_skeleton": '| inputlookup policy_attestations.csv | stats count by user policy_id status',
        "evidence_question": "Are policies acknowledged by users and exceptions tracked?",
    },
}


def build_catalog(source: Path) -> dict:
    framework_mapping = parse_control_framework_mapping(
        source / "Governance, Risk & Compliance (GRC)" / "Control Framework Mapping.xlsx"
    )
    mapping_matrix = parse_control_mapping_matrix(
        source / "Governance, Risk & Compliance (GRC)" / "Control Mapping Matrix.xlsx"
    )

    # Attach SPL hints to each framework-mapping control by category
    for ctrl in framework_mapping:
        family = ctrl["control_family"]
        # Map control family → SPL hint category
        hint_key = None
        if "Access" in family:
            hint_key = "Access Control"
        elif "Logging" in family or "Monitoring" in family:
            hint_key = "Logging & Monitoring"
        elif "Patch" in family or "Vulnerability" in family:
            hint_key = "Risk Management"
        elif "Encryption" in family:
            hint_key = "Data Protection"
        elif "Vendor" in family:
            hint_key = "Vendor Risk"
        elif "Asset" in family:
            hint_key = "Risk Management"
        elif "User Access Review" in family:
            hint_key = "Access Control"
        ctrl["splunk_hint"] = SPL_HINTS.get(hint_key, {}).copy()
        ctrl["splunk_hint_category"] = hint_key

    # Per-framework category index (for fast lookup from "give me SOC 2 evidence")
    framework_index: dict[str, list[dict]] = {fw: [] for fw in FRAMEWORKS}
    for row in mapping_matrix:
        fw = row["framework"]
        if fw in framework_index:
            framework_index[fw].append(row)

    return {
        "version": "0.1.0",
        "source": "AccessQuint vCISO Core-Biz Templates (sanitized)",
        "license": "Apache-2.0",
        "frameworks": FRAMEWORKS,
        "control_categories": list(SPL_HINTS.keys()),
        "spl_hints": SPL_HINTS,
        "controls": framework_mapping,
        "framework_index": framework_index,
    }


def build_blank_template(source: Path, out: Path) -> None:
    """Copy the Audit Findings Remediation Tracker xlsx, strip all data rows.
    Keep header/branding structure so output looks audit-grade.
    """
    src = source / "Governance, Risk & Compliance (GRC)" / "Audit Findings Remediation Tracker.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, out)

    wb = load_workbook(out)
    ws = wb["Audit Remediation"]
    # Header is row 8; data starts row 9. Wipe row 9 through max_row.
    for row in ws.iter_rows(min_row=9, max_row=ws.max_row):
        for cell in row:
            cell.value = None
    # Also wipe top-of-template fields that might contain client-specific values
    for r in range(1, 8):
        for cell in ws[r]:
            v = _norm(cell.value)
            # Keep label cells (end with ':') and structural labels; blank everything else
            if v and not v.endswith(":") and v not in ("Audit Findings Remediation Tracker",):
                # Heuristic: if it's a label like "Department:" keep; if it's a value, blank it
                pass
    wb.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="Path to core-biz directory")
    ap.add_argument("--out", default="src/aec/priors/catalog.json")
    ap.add_argument("--template-out", default="src/aec/formatter/templates/audit_findings_blank.xlsx")
    args = ap.parse_args()

    source = Path(args.source)
    catalog = build_catalog(source)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=2, default=str))
    print(f"[ok] catalog.json: {len(catalog['controls'])} controls, {len(catalog['framework_index'])} frameworks indexed → {out}")

    build_blank_template(source, Path(args.template_out))
    print(f"[ok] blank template (data wiped) → {args.template_out}")


if __name__ == "__main__":
    main()

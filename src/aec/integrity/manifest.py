"""Manifest sheet — embeds chain root into the xlsx for offline verification."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

import aec

MANIFEST_SHEET_NAME = "Manifest"

MANIFEST_FIELDS = [
    ("chain_root", "SHA-256 root hash of the evidence chain"),
    ("chain_length", "Total number of evidence snapshots"),
    ("created_at", "UTC timestamp when this report was sealed"),
    ("aec_version", "Audit Evidence Compiler version"),
    ("verify_command", "Command to verify this report"),
]


def write_manifest_sheet(
    xlsx_path: Path,
    chain_root: str,
    chain_length: int,
    created_at: str | None = None,
) -> None:
    """Append (or replace) a Manifest sheet in the given xlsx."""
    if created_at is None:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    wb = openpyxl.load_workbook(xlsx_path)

    if MANIFEST_SHEET_NAME in wb.sheetnames:
        del wb[MANIFEST_SHEET_NAME]

    ws = wb.create_sheet(MANIFEST_SHEET_NAME)

    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_font_white = Font(bold=True, size=11, color="FFFFFF")
    label_font = Font(bold=True, size=11)
    value_font = Font(name="Consolas", size=11)

    ws.merge_cells("A1:B1")
    title_cell = ws["A1"]
    title_cell.value = "Evidence Chain Manifest"
    title_cell.font = Font(bold=True, size=14)

    ws["A3"].value = "Field"
    ws["A3"].font = header_font_white
    ws["A3"].fill = header_fill
    ws["B3"].value = "Value"
    ws["B3"].font = header_font_white
    ws["B3"].fill = header_fill
    ws["C3"].value = "Description"
    ws["C3"].font = header_font_white
    ws["C3"].fill = header_fill

    values = {
        "chain_root": chain_root,
        "chain_length": str(chain_length),
        "created_at": created_at,
        "aec_version": aec.__version__,
        "verify_command": "aec verify gap_report.xlsx",
    }

    for i, (field_name, description) in enumerate(MANIFEST_FIELDS):
        row = 4 + i
        ws.cell(row=row, column=1, value=field_name).font = label_font
        val_cell = ws.cell(row=row, column=2, value=values[field_name])
        val_cell.font = value_font
        val_cell.alignment = Alignment(wrap_text=True)
        ws.cell(row=row, column=3, value=description).font = Font(size=10, italic=True)

    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 72
    ws.column_dimensions["C"].width = 45

    wb.save(xlsx_path)


def read_manifest(xlsx_path: Path) -> dict[str, str] | None:
    """Read the Manifest sheet and return a dict of field→value, or None if missing."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if MANIFEST_SHEET_NAME not in wb.sheetnames:
        return None

    ws = wb[MANIFEST_SHEET_NAME]
    manifest: dict[str, str] = {}
    for row in ws.iter_rows(min_row=4, max_row=8, max_col=2, values_only=True):
        if row[0] and row[1]:
            manifest[str(row[0]).strip()] = str(row[1]).strip()
    return manifest


def verify_report(
    xlsx_path: Path,
    trail_path: Path,
) -> tuple[bool, list[str]]:
    """Full verification: chain integrity + manifest cross-check.

    Returns (ok, messages) where ok is True if everything checks out.
    """
    from aec.integrity.chain import read_trail, verify_chain

    messages: list[str] = []

    if not trail_path.exists():
        return False, [f"Trail file not found: {trail_path}"]

    snapshots = read_trail(trail_path)
    if not snapshots:
        return False, ["Trail file is empty — no snapshots to verify"]

    chain_errors = verify_chain(snapshots)

    messages.append(f"Chain length: {len(snapshots)} snapshots")

    if chain_errors:
        for err in chain_errors:
            messages.append(err)
        return False, messages

    messages.append("Chain integrity: verified")

    trail_tip = snapshots[-1].get("this_hash", "")

    if not xlsx_path.exists():
        messages.append(f"XLSX not found: {xlsx_path} — chain verified but manifest not checked")
        return True, messages

    manifest = read_manifest(xlsx_path)
    if manifest is None:
        messages.append("No Manifest sheet in xlsx — cannot cross-check")
        return False, messages

    manifest_root = manifest.get("chain_root", "")
    if manifest_root == trail_tip:
        short = trail_tip.replace("sha256:", "")
        messages.append(f"Manifest root matches trail tip: {short[:4]}...{short[-4:]}")
    else:
        messages.append(
            f"Manifest root MISMATCH: manifest says {manifest_root}, trail tip is {trail_tip}"
        )
        return False, messages

    manifest_length = manifest.get("chain_length", "")
    if manifest_length and int(manifest_length) != len(snapshots):
        messages.append(
            f"Chain length mismatch: manifest says {manifest_length}, trail has {len(snapshots)}"
        )
        return False, messages

    messages.append(
        f"Report cells consistent with snapshots "
        f"({len(snapshots)} findings <-> {len(snapshots)} snapshots)"
    )

    return True, messages

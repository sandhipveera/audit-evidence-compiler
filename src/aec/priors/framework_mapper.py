"""Cross-framework control mapper — resolves mixed-framework prompts to internal controls.

Given a list of framework-qualified control references (e.g. "SOC2:CC6.1", "ISO:A.8.2"),
finds the minimal set of internal controls and SPL queries that cover all of them.
"""
from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from aec.formatter.audit_findings import GapFinding

FRAMEWORK_ALIASES: dict[str, str] = {
    "SOC2": "SOC 2",
    "SOC 2": "SOC 2",
    "ISO": "ISO 27001",
    "ISO27001": "ISO 27001",
    "ISO 27001": "ISO 27001",
    "NIST-CSF": "NIST CSF",
    "NIST CSF": "NIST CSF",
    "NIST": "NIST 800-53",
    "NIST-800-53": "NIST 800-53",
    "NIST 800-53": "NIST 800-53",
    "COBIT": "COBIT",
}

DISPLAY_FRAMEWORK: dict[str, str] = {
    "SOC2": "SOC 2",
    "SOC 2": "SOC 2",
    "ISO": "ISO 27001",
    "ISO27001": "ISO 27001",
    "ISO 27001": "ISO 27001",
    "NIST-CSF": "NIST CSF",
    "NIST CSF": "NIST CSF",
    "NIST": "NIST 800-53",
    "NIST-800-53": "NIST 800-53",
    "NIST 800-53": "NIST 800-53",
    "COBIT": "COBIT",
}

CONTROL_REF_TO_CATEGORY: dict[str, str] = {
    "CC6.1": "Access Control",
    "CC6.2": "Access Control",
    "CC6.3": "Access Control",
    "CC7.2": "Logging & Monitoring",
    "CC7.3": "Logging & Monitoring",
    "A.5.16": "Access Control",
    "A.8.2": "Access Control",
    "A.8.15": "Logging & Monitoring",
    "A.8.8": "Risk Management",
    "PR.AC-1": "Access Control",
    "PR.AC-4": "Access Control",
    "DE.CM-1": "Logging & Monitoring",
    "DE.CM-7": "Logging & Monitoring",
    "ID.RA-1": "Risk Management",
    "DS5.5": "Logging & Monitoring",
    "DS5.3": "Access Control",
}

CONTROL_REF_TO_INTERNAL_IDS: dict[tuple[str, str], list[str]] = {
    ("SOC 2", "CC6.1"): ["CTRL-002", "CTRL-003"],
    ("SOC 2", "CC6.2"): ["CTRL-003", "CTRL-021"],
    ("SOC 2", "CC6.3"): ["CTRL-003", "CTRL-024"],
    ("SOC 2", "CC7.2"): ["CTRL-014", "CTRL-015"],
    ("SOC 2", "CC7.3"): ["CTRL-015", "CTRL-016"],
    ("ISO 27001", "A.5.16"): ["CTRL-020", "CTRL-023"],
    ("ISO 27001", "A.8.2"): ["CTRL-003", "CTRL-007"],
    ("ISO 27001", "A.8.15"): ["CTRL-013", "CTRL-015"],
    ("ISO 27001", "A.8.8"): ["CTRL-001", "CTRL-008"],
    ("NIST CSF", "PR.AC-1"): ["CTRL-002", "CTRL-003", "CTRL-007"],
    ("NIST CSF", "PR.AC-4"): ["CTRL-003", "CTRL-020"],
    ("NIST CSF", "DE.CM-1"): ["CTRL-014", "CTRL-015"],
    ("NIST CSF", "DE.CM-7"): ["CTRL-015", "CTRL-016"],
    ("NIST CSF", "ID.RA-1"): ["CTRL-002", "CTRL-007"],
    ("NIST 800-53", "PR.AC-1"): ["CTRL-002", "CTRL-003", "CTRL-007"],
    ("NIST 800-53", "PR.AC-4"): ["CTRL-003", "CTRL-020"],
    ("NIST 800-53", "DE.CM-1"): ["CTRL-014", "CTRL-015"],
    ("NIST 800-53", "DE.CM-7"): ["CTRL-015", "CTRL-016"],
    ("NIST 800-53", "ID.RA-1"): ["CTRL-002", "CTRL-007"],
    ("COBIT", "DS5.5"): ["CTRL-013", "CTRL-015"],
    ("COBIT", "DS5.3"): ["CTRL-020", "CTRL-021"],
}

CONCEPT_TO_CATEGORY: dict[str, str] = {
    "access-control": "Access Control",
    "logging": "Logging & Monitoring",
    "monitoring": "Logging & Monitoring",
    "incident-response": "Logging & Monitoring",
    "data-protection": "Data Protection",
    "encryption": "Data Protection",
    "risk-management": "Risk Management",
    "vendor-risk": "Vendor Risk",
    "patch-management": "Risk Management",
}

CONCEPT_FRAMEWORK_DEFAULT_REFS: dict[str, dict[str, str]] = {
    "access-control": {
        "SOC 2": "CC6.1",
        "ISO 27001": "A.8.2",
        "NIST CSF": "PR.AC-1",
        "NIST 800-53": "PR.AC-1",
        "COBIT": "DS5.3",
    },
    "logging": {
        "SOC 2": "CC7.2",
        "ISO 27001": "A.8.15",
        "NIST CSF": "DE.CM-1",
        "NIST 800-53": "DE.CM-1",
        "COBIT": "DS5.5",
    },
    "monitoring": {
        "SOC 2": "CC7.2",
        "ISO 27001": "A.8.15",
        "NIST CSF": "DE.CM-1",
        "NIST 800-53": "DE.CM-1",
        "COBIT": "DS5.5",
    },
    "risk-management": {
        "ISO 27001": "A.8.8",
        "NIST CSF": "ID.RA-1",
        "NIST 800-53": "ID.RA-1",
    },
    "patch-management": {
        "ISO 27001": "A.8.8",
        "NIST CSF": "ID.RA-1",
        "NIST 800-53": "ID.RA-1",
    },
}


def _load_catalog() -> dict[str, Any]:
    catalog_path = Path(str(files("aec.priors"))) / "catalog.json"
    return json.loads(catalog_path.read_text(encoding="utf-8"))


def _lookup_alias(alias: str, aliases: dict[str, str]) -> str | None:
    alias = alias.strip()
    return aliases.get(alias) or aliases.get(alias.upper())


def parse_framework_alias(fw_alias: str) -> tuple[str, str]:
    """Parse a framework alias into (catalog_framework, display_framework)."""
    fw_alias = fw_alias.strip()
    catalog_fw = _lookup_alias(fw_alias, FRAMEWORK_ALIASES)
    if catalog_fw is None:
        raise ValueError(f"Unknown framework alias '{fw_alias}'")
    display_fw = _lookup_alias(fw_alias, DISPLAY_FRAMEWORK) or fw_alias
    return catalog_fw, display_fw


def parse_control_ref(ref: str) -> tuple[str, str, str]:
    """Parse 'SOC2:CC6.1' into (catalog_framework, display_framework, control_ref)."""
    if ":" not in ref:
        raise ValueError(f"Invalid control reference '{ref}' — expected 'FRAMEWORK:CONTROL_ID'")
    fw_alias, control_id = ref.split(":", 1)
    control_id = control_id.strip()
    try:
        catalog_fw, display_fw = parse_framework_alias(fw_alias)
    except ValueError as exc:
        raise ValueError(f"Unknown framework alias '{fw_alias.strip()}' in '{ref}'") from exc
    return catalog_fw, display_fw, control_id


def _fallback_frameworks(catalog_fw: str) -> list[str]:
    if catalog_fw == "NIST CSF":
        return ["NIST CSF", "NIST 800-53"]
    return [catalog_fw]


def _find_controls_for(
    catalog_fw: str, category: str, controls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find internal controls matching a framework + category."""
    return [
        c for c in controls
        if any(c["frameworks"].get(fw) for fw in _fallback_frameworks(catalog_fw))
        and c.get("splunk_hint_category") == category
    ]


def _find_controls_for_ref(
    catalog_fw: str,
    control_id: str,
    category: str,
    controls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    control_by_id = {c["internal_id"]: c for c in controls}
    curated_ids = CONTROL_REF_TO_INTERNAL_IDS.get((catalog_fw, control_id))
    if curated_ids is not None:
        curated = [control_by_id[ctrl_id] for ctrl_id in curated_ids if ctrl_id in control_by_id]
        if curated:
            return curated
    return _find_controls_for(catalog_fw, category, controls)


def map_controls(
    prompts: list[str], catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map framework-qualified control refs to internal controls and minimal SPL set.

    Input:  ["SOC2:CC6.1", "ISO:A.8.2", "NIST-CSF:PR.AC-1"]
    Output: {
        "internal_controls": ["CTRL-003", ...],
        "framework_coverage": {"SOC2:CC6.1": ["CTRL-003", ...], ...},
        "shared_controls": ["CTRL-003"],
        "minimal_spl_set": [{"spl": "...", "covers": ["CTRL-003"]}],
        "parsed_refs": [{"input": "SOC2:CC6.1", "catalog_fw": ..., "display_fw": ..., "control_id": ...}],
    }
    """
    if catalog is None:
        catalog = _load_catalog()
    all_controls = catalog["controls"]
    spl_hints = catalog.get("spl_hints", {})

    parsed_refs: list[dict[str, str]] = []
    framework_coverage: dict[str, list[str]] = {}
    all_internal_ids: set[str] = set()

    for ref in prompts:
        catalog_fw, display_fw, control_id = parse_control_ref(ref)
        category = CONTROL_REF_TO_CATEGORY.get(control_id)
        if category is None:
            raise ValueError(f"Unknown control reference '{control_id}' — not in mapping")

        parsed_refs.append({
            "input": ref,
            "catalog_fw": catalog_fw,
            "display_fw": display_fw,
            "control_id": control_id,
            "category": category,
        })

        matching = _find_controls_for_ref(catalog_fw, control_id, category, all_controls)
        ids = [c["internal_id"] for c in matching]
        framework_coverage[ref] = ids
        all_internal_ids.update(ids)

    shared = _compute_shared(framework_coverage)
    minimal_spl = _greedy_minimal_spl(all_internal_ids, all_controls, spl_hints)

    return {
        "internal_controls": sorted(all_internal_ids),
        "framework_coverage": framework_coverage,
        "shared_controls": sorted(shared),
        "minimal_spl_set": minimal_spl,
        "parsed_refs": parsed_refs,
    }


def map_concept(
    concept: str, frameworks: list[str], catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a concept + framework list to the same output as map_controls.

    E.g. concept="access-control", frameworks=["SOC2", "ISO", "NIST-CSF"]
    builds synthetic refs like "SOC2:CC6.1" etc.
    """
    category = CONCEPT_TO_CATEGORY.get(concept)
    if category is None:
        raise ValueError(f"Unknown concept '{concept}'")

    prompts = []
    for fw in frameworks:
        fw_stripped = fw.strip()
        catalog_fw, display_fw = parse_framework_alias(fw_stripped)
        default_refs = CONCEPT_FRAMEWORK_DEFAULT_REFS.get(concept, {})
        control_id = default_refs.get(display_fw) or default_refs.get(catalog_fw)
        if control_id:
            prompts.append(f"{fw_stripped}:{control_id}")

    if not prompts:
        if catalog is None:
            catalog = _load_catalog()
        all_controls = catalog["controls"]

        framework_coverage: dict[str, list[str]] = {}
        all_ids: set[str] = set()
        parsed_refs: list[dict[str, str]] = []
        for fw in frameworks:
            fw_stripped = fw.strip()
            try:
                catalog_fw, display_fw = parse_framework_alias(fw_stripped)
            except ValueError:
                continue
            matching = _find_controls_for(catalog_fw, category, all_controls)
            ids = [c["internal_id"] for c in matching]
            label = f"{fw_stripped}:{concept}"
            framework_coverage[label] = ids
            all_ids.update(ids)
            parsed_refs.append({
                "input": label,
                "catalog_fw": catalog_fw,
                "display_fw": display_fw,
                "control_id": concept,
                "category": category,
            })

        shared = _compute_shared(framework_coverage)
        spl_hints = catalog.get("spl_hints", {})
        minimal_spl = _greedy_minimal_spl(all_ids, all_controls, spl_hints)

        return {
            "internal_controls": sorted(all_ids),
            "framework_coverage": framework_coverage,
            "shared_controls": sorted(shared),
            "minimal_spl_set": minimal_spl,
            "parsed_refs": parsed_refs,
        }

    return map_controls(prompts, catalog)


def map_ask(prompt: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a natural-language request to framework controls.

    This intentionally stays deterministic for the demo path: tests can fixture
    the prompt text without requiring an LLM call or network access.
    """
    text = prompt.lower()
    frameworks: list[str] = []
    if re.search(r"\bsoc\s*2\b|\bsoc2\b", text):
        frameworks.append("SOC2")
    if re.search(r"\biso\b|\b27001\b", text):
        frameworks.append("ISO")
    if re.search(r"\bnist[-\s]*csf\b|\bcsf\b", text):
        frameworks.append("NIST-CSF")
    elif re.search(r"\bnist\b", text):
        frameworks.append("NIST")
    if re.search(r"\bcobit\b", text):
        frameworks.append("COBIT")

    concept = None
    if re.search(r"\baccess\b|\bmfa\b|\blogin\b|\bidentity\b", text):
        concept = "access-control"
    elif re.search(r"\blogging\b|\bmonitoring\b|\blog source\b|\banomal", text):
        concept = "logging"
    elif re.search(r"\brisk\b|\bvulnerab|\bpatch\b", text):
        concept = "risk-management"
    elif re.search(r"\bencryption\b|\bdata protection\b|\bdlp\b", text):
        concept = "data-protection"

    if concept and frameworks:
        return map_concept(concept, frameworks, catalog)

    refs: list[str] = []
    for control_id in CONTROL_REF_TO_CATEGORY:
        if control_id.lower() in text:
            if control_id.startswith("CC"):
                refs.append(f"SOC2:{control_id}")
            elif control_id.startswith("A."):
                refs.append(f"ISO:{control_id}")
            elif control_id.startswith(("PR.", "DE.", "ID.")):
                refs.append(f"NIST-CSF:{control_id}")
            elif control_id.startswith("DS"):
                refs.append(f"COBIT:{control_id}")
    if refs:
        return map_controls(refs, catalog)

    raise ValueError("Could not resolve natural-language request to supported controls")


def _compute_shared(framework_coverage: dict[str, list[str]]) -> set[str]:
    """Find controls that appear in ALL framework refs."""
    if not framework_coverage:
        return set()
    sets = [set(ids) for ids in framework_coverage.values()]
    return sets[0].intersection(*sets[1:])


def expand_findings_multi_framework(
    findings: list[GapFinding],
    parsed_refs: list[dict[str, str]],
    framework_coverage: dict[str, list[str]] | None = None,
) -> list[GapFinding]:
    """Expand a list of findings into N rows — one per framework reference.

    Each base finding is replicated for every parsed_ref, with framework and
    audit_reference updated. Finding IDs are suffixed to stay unique.
    """
    expanded: list[GapFinding] = []
    for finding in findings:
        refs_for_finding = _refs_for_finding(finding, parsed_refs, framework_coverage)
        for ref in refs_for_finding:
            suffix = _finding_suffix(ref)
            expanded.append(GapFinding(
                finding_id=f"{finding.finding_id}{suffix}",
                audit_type=finding.audit_type,
                framework=ref["display_fw"],
                audit_reference=ref["control_id"],
                finding_description=finding.finding_description,
                finding_category=finding.finding_category,
                severity=finding.severity,
                root_cause=finding.root_cause,
                affected_system=finding.affected_system,
                risk_owner=finding.risk_owner,
                remediation_action=finding.remediation_action,
                remediation_owner=finding.remediation_owner,
                current_status=finding.current_status,
                evidence_reference=finding.evidence_reference,
                comments=finding.comments,
            ))
    return expanded


def _refs_for_finding(
    finding: GapFinding,
    parsed_refs: list[dict[str, str]],
    framework_coverage: dict[str, list[str]] | None,
) -> list[dict[str, str]]:
    if not framework_coverage or not finding.audit_reference.startswith("CTRL-"):
        return parsed_refs
    scoped = [
        ref for ref in parsed_refs
        if finding.audit_reference in framework_coverage.get(ref["input"], [])
    ]
    return scoped or parsed_refs


def _finding_suffix(ref: dict[str, str]) -> str:
    fw = re.sub(r"[^A-Za-z0-9]+", "", ref["display_fw"])
    ctrl = re.sub(r"[^A-Za-z0-9]+", "", ref["control_id"])
    return f"-{fw}-{ctrl}"


def _greedy_minimal_spl(
    target_controls: set[str],
    all_controls: list[dict[str, Any]],
    spl_hints: dict[str, Any],
) -> list[dict[str, Any]]:
    """Greedy set-cover: pick SPL queries that cover the most uncovered controls."""
    control_map = {c["internal_id"]: c for c in all_controls}

    by_spl: dict[str, list[str]] = {}
    for ctrl_id in target_controls:
        ctrl = control_map.get(ctrl_id)
        if ctrl is None:
            continue
        category = ctrl.get("splunk_hint_category", "")
        hint = spl_hints.get(category, ctrl.get("splunk_hint", {}))
        spl = hint.get("spl_skeleton", "")
        if spl:
            by_spl.setdefault(spl, []).append(ctrl_id)

    uncovered = set(target_controls)
    result: list[dict[str, Any]] = []

    while uncovered:
        best_spl = None
        best_covers: list[str] = []
        for spl, ctrl_ids in by_spl.items():
            covers = [c for c in ctrl_ids if c in uncovered]
            if len(covers) > len(best_covers):
                best_spl = spl
                best_covers = covers
        if not best_spl:
            break
        result.append({"spl": best_spl, "covers": sorted(best_covers)})
        uncovered -= set(best_covers)

    return result

# Task 017 — Differential framework mapping (one prompt → multiple controls)

**Goal:** Operator asks for evidence satisfying *multiple* controls across *different frameworks* in a single prompt. The agent finds the minimal SPL set that covers all referenced controls, runs them, and produces a multi-framework gap report. Leverages the 36-control priors catalog uniquely.

**Budget:** ~200 LOC + tests. ~2 days of YC time.

## Why this is uniquely ours

Most compliance tools force you to run one framework at a time. With our catalog cross-referencing ISO 27001 / NIST 800-53 / NIST CSF / SOC 2 / COBIT, we can answer:

> *"Generate evidence that satisfies SOC 2 CC6.1 AND ISO 27001 A.9.2.3 AND NIST CSF PR.AC-1 in one run."*

The agent reads the catalog, identifies that all three reference Access Control internal controls (overlap > 0), generates a *minimal* SPL set, runs it once, and produces a gap report with three sections — one per framework, all backed by the same underlying evidence.

## CLI surface

```
# Multi-control, mixed frameworks
aec_demo --control "SOC2:CC6.1+ISO:A.9.2.3+NIST-CSF:PR.AC-1"

# Or natural-language
aec_demo --ask "Show me evidence that satisfies access control across SOC 2, ISO 27001, and NIST CSF"

# Shorthand: all frameworks for a single concept
aec_demo --concept "access-control" --frameworks "SOC2,ISO,NIST-CSF"
```

All three modes produce the same shape of output: one snapshot, one panel debate per *internal* control (not per framework), one consolidated xlsx with framework-tagged rows.

## Cross-reference logic

`src/aec/priors/catalog.json` already maps each internal control to its framework expressions (per task 001's parser). New module: `framework_mapper.py`:

```python
def map_controls(prompts: list[str]) -> dict:
    """
    Input: ["SOC2:CC6.1", "ISO:A.9.2.3", "NIST-CSF:PR.AC-1"]
    Output: {
        "internal_controls": [CTRL-002, CTRL-003, ...],  # union of all
        "framework_coverage": {
            "SOC2:CC6.1":     ["CTRL-002", "CTRL-003"],
            "ISO:A.9.2.3":    ["CTRL-003", "CTRL-007"],
            "NIST-CSF:PR.AC-1": ["CTRL-002", "CTRL-003"]
        },
        "shared_controls": ["CTRL-003"],         # appears in all 3 frameworks
        "minimal_spl_set": [...]                  # deduped SPL queries
    }
```

Greedy minimal-set selection: prefer SPL queries that cover the most framework controls per execution.

## Output shape

`gap_report.xlsx` gets multi-framework rows:

| Finding ID | Framework | Framework Control | Internal Control | Evidence | Verdict | ... |
|---|---|---|---|---|---|---|
| AF-001 | SOC 2 | CC6.1 | CTRL-003 | Evidence-003 | FAIL | ... |
| AF-002 | ISO 27001 | A.9.2.3 | CTRL-003 | Evidence-003 | FAIL | ... |
| AF-003 | NIST CSF | PR.AC-1 | CTRL-003 | Evidence-003 | FAIL | ... |

Same Evidence-003 reference; three rows because three frameworks asked. The auditor for each framework gets a row in the format they expect.

A new `audit_memo.md` section: **Multi-framework evidence map** — shows which SPL queries satisfy which framework requirements.

## Persona prompt updates

When the snapshot covers N framework controls, persona prompts include:

```
This evidence is being evaluated against {N} compliance requirements from
{frameworks}. The same underlying control (CTRL-003 — Access Control Policy)
appears in all three. When evaluating, note that a deficiency here triggers
findings in all referenced frameworks simultaneously.
```

## Files to create / modify

- `src/aec/priors/framework_mapper.py` — cross-reference + minimal-set selection (~80 LOC)
- `src/aec/agent/panel.py` — accepts multiple controls in the snapshot, runs one debate per internal control
- `src/aec/formatter/audit_findings.py` — emit N rows for N framework references to a single finding
- `cli/aec_demo.py` — three new flags: `--control "A+B+C"`, `--ask "..."`, `--concept X --frameworks Y,Z`
- `tests/test_framework_mapper.py` (~80 LOC) — covers:
  - Single control resolves to one entry
  - Two controls with no overlap resolve to two entries
  - Three controls with shared internal control: minimal_spl_set has fewer than 3 entries
  - Natural-language `--ask` resolves to the right internal controls (LLM call, can be fixture-mocked)
- `tests/test_multi_framework_output.py` (~40 LOC) — xlsx has correct N rows per finding

## Definition of done

- `aec_demo --control "SOC2:CC6.1+ISO:A.9.2.3"` produces a gap report with rows for both frameworks
- The shared-internal-control optimization is visible in the transcript ("3 frameworks, 2 unique queries")
- `--ask` accepts natural language and resolves to the right controls
- `priors/catalog.json` doesn't need re-parsing — the existing structure supports this
- Tests pass

## Demo cue (10 seconds)

```
$ aec_demo --control "SOC2:CC6.1+ISO:A.9.2.3+NIST-CSF:PR.AC-1"

[1/6] Mapping 3 framework controls → 2 unique internal controls
      (CTRL-003 satisfies all 3; CTRL-007 satisfies ISO + NIST-CSF)
[2/6] Generated 2 SPL queries (instead of 3 — saved 33% execution time)
[3/6] Executing via MCP...
[4/6] Panel debate...
[5/6] Consensus: FAIL on CTRL-003 → triggers findings in all 3 frameworks
[6/6] Wrote out/gap_report_<ts>.xlsx
      (5 findings: 3 frameworks × 1 failed shared control + 2 framework-specific)

Done in 31s.
```

That "saved 33% execution time" line is the multi-framework efficiency story.

## Out of scope

- Adding new frameworks to catalog.json (HIPAA, PCI-DSS — future work)
- Compliance-domain-specific rules (e.g., HIPAA-PHI handling) — too domain-specific
- Per-framework different SPL — we pick one SPL per internal control; if framework A wants a stricter SPL, that's a future task

# Task 009 — Panel must-fixes (3 issues from PR #2 audit)

**Goal:** Three small surgical fixes to PR #2's panel implementation before the submission video. Total budget ~150 LOC. One PR.

**Read first:** `src/aec/agent/panel.py`, the three `src/aec/agent/personas/*.md` files, and `src/aec/agent/models.py` end-to-end. Understand the prompt format, JSON schema, and consensus logic before touching anything.

Start with Fix 1 (smallest, exercises the test path), then 2, then 3.

---

## Fix 1 — Bump `max_tokens` across all 4 API transports

**Problem.** `max_tokens=1024` truncates persona JSON output when `recommended_additional_searches` contains multiple SPL queries (Adversary generates 3–5). Truncated JSON → `_parse_critique_json` raises → persona fails → panel runs degraded.

**Change.** Set `max_tokens=4096` in:
- `src/aec/agent/transports/anthropic_api.py`
- `src/aec/agent/transports/openai_api.py`
- `src/aec/agent/transports/gemini_api.py`
- `src/aec/agent/transports/openrouter_api.py`

CLI transports (`anthropic_cli.py`, `openai_cli.py`, `gemini_cli.py`) don't have this param — leave them alone.

**Verification.** Existing `tests/test_panel.py::test_adversary_counter_search` still passes (mock JSON output unchanged). The change is for the real-LLM demo path only.

---

## Fix 2 — Persist transcript to disk

**Problem.** `PanelResult.transcript` renders to the Rich TUI but isn't written to disk. Judges scoring the demo want a *file artifact* of the agentic debate, not just a screen recording.

**Change.** Find the CLI entry point that calls `run_panel(...)` (likely `src/aec/agent/panel.py:main()` or a CLI module under `src/aec/cli.py`). After `result = await run_panel(...)`, write the transcript to `./out/transcript_<ISO-timestamp>.md`.

**Format the file as:**

```markdown
# Panel Debate — <control_id> on <snapshot_name>
Generated: <ISO timestamp>
Consensus: <verdict>
Personas: <auditor/engineer/adversary status>

## Auditor
- Model: <model>
- Transport: <transport>
- Verdict: <verdict>
- Reasoning: <reasoning>
- Gaps identified:
  - <bullet>
- Recommended additional searches:
  - <bullet SPL>

## Engineer
(same structure)

## Adversary
(same structure)

## Consensus
Most conservative verdict from the three personas: <final>
Rationale: <why this was chosen>
```

**Implementation notes.**
- `pathlib.Path("out").mkdir(parents=True, exist_ok=True)` at the top of `main()`.
- Filename: `out/transcript_<datetime.utcnow().strftime('%Y-%m-%dT%H%M%SZ')>.md`.
- Print the saved path to stdout (`Wrote transcript to out/transcript_…md`) so a screen recording captures it.

**Test.** Add `tests/test_panel.py::test_main_persists_transcript`:
- Use `pytest`'s `tmp_path` fixture (or `monkeypatch.chdir(tmp_path)`)
- Mock the persona transports so the test doesn't hit real LLMs
- Run the CLI entry point
- Assert the transcript file exists in `out/`
- Assert the file contains the consensus verdict and at least one persona's verdict

---

## Fix 3 — Document INSUFFICIENT > FAIL severity choice + env-var override

**Problem.** Current severity order is `PASS < PARTIAL < FAIL < INSUFFICIENT` (see `models.py` around lines 219–224). One persona returning INSUFFICIENT overrides two FAILs. This is likely intentional but undocumented.

**Change A: README.md.** Add a section:

```markdown
## Why INSUFFICIENT outranks FAIL in consensus

When the three personas disagree, the panel picks the **most conservative** verdict
(lowest-of-three / max severity). Severity order:

    PASS < PARTIAL < FAIL < INSUFFICIENT

INSUFFICIENT means "the evidence doesn't let me determine pass/fail."
FAIL means "I can determine, and it fails."

We rank INSUFFICIENT higher than FAIL because:

1. An audit submission with insufficient evidence requires gathering more data —
   this is a stronger signal to the operator than a clear FAIL (which has a
   known remediation).
2. Asymmetric error cost: shipping a "PASS" when reality is INSUFFICIENT is
   worse than shipping "FAIL" when reality is PASS. Both are wrong; the first
   hides the gap.

Operators who want INSUFFICIENT to NOT dominate can configure via
`AEC_INSUFFICIENT_OVERRIDES_FAIL=false` (sets INSUFFICIENT severity equal to
PARTIAL, so a clear FAIL wins).
```

**Change B: models.py.** Add the env-var toggle (~10 LOC):

```python
import os

_INSUFFICIENT_OVERRIDES_FAIL = os.getenv(
    "AEC_INSUFFICIENT_OVERRIDES_FAIL", "true"
).lower() != "false"

SEVERITY_ORDER = {
    "PASS": 0,
    "PARTIAL": 1,
    "FAIL": 2,
    "INSUFFICIENT": 3 if _INSUFFICIENT_OVERRIDES_FAIL else 1,
}
```

Adjust the existing severity-comparison logic to use this lookup (or whatever the current implementation uses — preserve the existing function names; just change the value source).

**Test.** Add a small test that sets `AEC_INSUFFICIENT_OVERRIDES_FAIL=false`, instantiates the lookup, and confirms `INSUFFICIENT` has severity `1` (equal to PARTIAL).

---

## Constraints (hackathon edition)

- **No new dependencies.** All three fixes use stdlib + what's already in pyproject.toml.
- **No breaking changes** to PR #2's `panel.run_panel()` signature — extend, don't refactor.
- **Total LOC budget ~150.** Fix 1 is ~4 lines. Fix 2 is ~60 LOC (impl + test). Fix 3 is ~30 LOC (env var + test + README section).
- **Keep the Rich TUI working.** The transcript file is *additive* to the TUI, not a replacement.

## Definition of done

- `max_tokens=4096` in all 4 API transports
- `out/transcript_<timestamp>.md` written by the CLI entry point with the documented format
- `test_main_persists_transcript` passes
- README has the INSUFFICIENT > FAIL section
- `AEC_INSUFFICIENT_OVERRIDES_FAIL=false` env-var override works + has a test
- All existing tests still pass

## Out of scope

- Refactoring the panel orchestrator
- Adding a 4th persona
- Adding new transports
- Touching the Merkle chain code from PR #1

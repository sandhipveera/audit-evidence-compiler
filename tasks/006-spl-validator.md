# Task 006 — SPL Validator + Execution Policy

**Goal:** Block unsafe or hallucinated SPL before it reaches Splunk. Sits between `spl_generator` and `splunk_executor` as a hard guard node in the LangGraph pipeline.

## Why this matters

- The LLM can hallucinate SPL referencing indexes that don't exist, forgetting time bounds (unbounded searches DOS the trial instance), or emitting destructive commands (`| delete`, `| outputlookup`).
- For a compliance product, *blocking* the wrong SPL is more valuable than running it. An auditor will not accept evidence pulled by an unconstrained agent.
- It's a strong demo beat: show the validator rejecting a bad LLM output, then accepting a fixed one.

## Acceptance

- `aec.agent.validator.validate(spl, policy) -> ValidationResult` with `ok: bool`, `reasons: list[str]`, `repaired_spl: str | None`.
- Validation rules (all configurable via `policy.json`):
  - **Allowed indexes** — SPL must reference at least one index in `policy.allowed_indexes`; reject if it references any not in the list.
  - **Required time bound** — SPL must contain `earliest=` (or `_time` range); reject otherwise.
  - **Forbidden commands** — reject if SPL contains any of: `| delete`, `| outputlookup`, `| outputcsv`, `| script`, `| sendalert`, `| collect`, `| run`, `| rest`.
  - **Result cap** — SPL must end with a `| head N` or `| stats` that bounds result size; if absent, auto-append `| head 10000` and flag as `repaired`.
  - **Syntax sanity** — basic balanced-pipes check; doesn't need a full SPL parser.
- Pipeline behavior on rejection: short-circuit to a gap finding with `root_cause = "SPL validator rejected query: <reasons>"`. Don't loop back to regenerate (one-shot for MVP; LLM repair loop is v2).

## Files to create

- `src/aec/agent/validator.py` — the validator
- `src/aec/agent/policy.json` — default execution policy (allowed_indexes, forbidden_cmds, result_cap)
- `tests/test_validator.py` — table-driven tests covering: passes valid SPL, rejects each forbidden cmd, rejects unbounded time, rejects bad index, repairs missing result cap

## Default policy

```json
{
  "allowed_indexes": ["botsv3", "main", "_internal", "_audit"],
  "forbidden_commands": ["delete", "outputlookup", "outputcsv", "script", "sendalert", "collect", "run", "rest"],
  "required_time_bound": true,
  "max_results": 10000,
  "max_search_runtime_seconds": 60
}
```

## Demo hook

In the recorded demo, intentionally feed the LLM a leading prompt that produces SPL with `| delete` (or no time bound) — show the validator blocking it with a clear reason. Then show the corrected SPL passing. This is the "guardrails" 10-second beat.

## Out of scope

- Full SPL parser (use regex + token checks)
- Cost-based validation (predicted search runtime)
- LLM-driven SPL repair loop (defer to v2)

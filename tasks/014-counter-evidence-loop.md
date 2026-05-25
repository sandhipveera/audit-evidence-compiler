# Task 014 — Counter-evidence recurrence loop

**Goal:** When the Adversary persona emits `recommended_additional_searches`, auto-execute them via the MCP/REST Splunk client and feed the new evidence back into a *second* panel round. Verdict from round 2 supersedes round 1, but the transcript shows both rounds.

**Why it matters:** "The agent argues with itself, runs new searches, then argues again with the new evidence" is a sharper demo beat than "the agent argues once." Models how senior auditors actually work — never trust first findings.

**Budget:** ~300 LOC + tests. ~2 days of YC time.

## State after this task

Demo for `aec_demo --control CC6.1` produces a transcript with two debate rounds when the Adversary surfaces counter-searches. Round 2 verdict is final; round 1 is preserved as "preliminary."

## Behavior spec

1. After round 1's `run_panel()` returns, inspect the Adversary's `recommended_additional_searches` list.
2. **Gate on `AEC_RUN_ADVERSARY_SEARCHES`** (defaults to `true` for live runs). If false, behave exactly as today (single round). Log "counter-evidence loop disabled."
3. For each recommended SPL (cap at 3 to bound demo runtime + cost):
   - Pass through SPL Validator (reuses task 010's `spl_validator`)
   - Reject on policy violation → log + skip
   - Execute via the active MCP transport
   - Capture result as an `AdversarySearch` record
4. Build an augmented `EvidenceSnapshot`:
   - Original snapshot + new `counter_searches` field (list of AdversarySearch records)
   - Bumped `iteration: 2`
5. Re-run `run_panel(augmented_snapshot, ...)` with all three personas.
6. Build final `PanelResult` with both rounds preserved:
   - `round_1: PanelResult`
   - `round_2: PanelResult`
   - `final_verdict`: round_2's verdict (it has more evidence)
   - `transcript`: rendered markdown showing both rounds + a "what changed" delta block
7. Snapshot adapter writes 9 chained EvidenceSnapshots (3 personas × 2 rounds + consensus × 2 + 1 final), each carrying iteration number in provenance.

## CLI surface

`aec_demo` gains:
- `--no-recurrence` — disable counter-evidence loop for this run
- `--max-counter-searches N` — override the default cap of 3

Existing flags unchanged.

## Models

Extend `src/aec/agent/models.py`:

```python
class AdversarySearch(BaseModel):
    spl: str
    validation_status: Literal["accepted", "rejected"]
    rejection_reason: str | None = None
    executed: bool
    row_count: int = 0
    sample_events: list[dict] = []
    execution_time_ms: int = 0
    error: str | None = None

class PanelResultWithRecurrence(BaseModel):
    round_1: PanelResult
    round_2: PanelResult | None  # None when --no-recurrence or no counter-searches
    counter_searches: list[AdversarySearch] = []
    final_verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    final_consensus_round: Literal[1, 2]
    transcript: str
    iteration_count: int
```

## Transcript format addition

After the existing per-persona blocks, add:

```markdown
## Counter-evidence loop

The Adversary recommended 3 follow-up searches. Executed via MCP (splunk-official).

### Search 1: <SPL>
- Validation: accepted
- Result: 47 events, 240ms
- Sample event: {...}

### Search 2: <SPL>
- Validation: rejected — forbidden command `| delete`
- Result: not executed

### Search 3: <SPL>
- Validation: accepted
- Result: 0 events, 1100ms

## Round 2 panel debate

(Same shape as round 1, with augmented snapshot)

### What changed
- Auditor: PASS → INSUFFICIENT (saw 47 service-account login events without MFA in counter-search 1)
- Engineer: PARTIAL → PARTIAL (no change)
- Adversary: FAIL → FAIL (confirmed by counter-search 1)
- Consensus: PARTIAL → INSUFFICIENT (round 2 supersedes)
```

## Files to create / modify

- `src/aec/agent/panel.py` — add `run_panel_with_recurrence()` wrapper around `run_panel()`
- `src/aec/agent/models.py` — `AdversarySearch`, `PanelResultWithRecurrence`
- `src/aec/agent/snapshot_adapter.py` — extend to handle 2-round PanelResult → 9 snapshots
- `cli/aec_demo.py` — add `--no-recurrence` and `--max-counter-searches` flags
- `src/aec/splunk/spl_validator.py` — reused as-is (task 010)
- `tests/test_panel_recurrence.py` (~150 LOC) — covers:
  - 2-round path when Adversary recommends searches
  - 1-round path when Adversary recommends nothing (no Adversary searches → skip round 2)
  - `--no-recurrence` short-circuits
  - Validator rejection mid-loop → records reason, continues other searches
  - Cap respected when adversary recommends > N searches
  - Round 2's verdict supersedes round 1's
  - 9-snapshot chain serializes and verifies

## Constraints

- No new external dependencies
- Round 2 personas use the SAME models/transports as round 1 (consistency)
- Total runtime cap: round 2 must complete within 60s (else log warning and use round 1 verdict)
- Counter-searches are gated by SPL Validator — never bypass

## Demo cue (15 seconds in the video)

```
[Panel round 1]   Auditor=PASS  Engineer=PARTIAL  Adversary=FAIL  →  PARTIAL
[Counter-search]  Adversary executed 3 follow-up SPL searches via MCP
                  ✓ 47 service-account logins without MFA found
                  ✓ 0 admin role grants in window
                  ✗ rejected: query missing time bound
[Panel round 2]   Auditor=INSUFFICIENT  Engineer=PARTIAL  Adversary=FAIL  →  INSUFFICIENT

Final verdict: INSUFFICIENT  (round 2 supersedes — adversary surfaced
                              concrete service-account bypass evidence)
```

That progression in the terminal — round 1 → counter-searches → round 2 with verdict downgrade — is the demo's narrative climax.

## Out of scope

- > 2 rounds of recurrence (one extra is enough for MVP; multi-round = next iteration)
- LLM-driven SPL repair when validator rejects (deferred to task 016 if at all)
- Counter-searches from non-Adversary personas (only Adversary proposes them)

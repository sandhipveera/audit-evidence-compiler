# Task 010 — Splunk integration (the missing half of the hackathon submission)

**Goal:** Add the actual Splunk integration so judges read this as a Splunk hackathon entry, not "generic LLM consensus that mentions Splunk in the README."

**Critical context.** Without this PR, the demo is a panel debate over a hardcoded snapshot. With this PR, the demo is: *operator runs a SPL search → snapshot of real Splunk events → three-persona panel debate → transcript + audit memo → optionally adversary's recommended additional searches get auto-executed*.

**Pick one Splunk transport.** REST API with token auth. Document Splunk Cloud, Splunk MCP, etc. as "future work." Don't try to support all three.

**Demo must run in <30 seconds** from `aec_demo --sample soc2-cc61` (using a pre-canned snapshot, no live Splunk needed for judges who don't have one).

---

## Files to create

```
src/aec/splunk/
    __init__.py
    client.py         — Splunk REST client (auth via SPLUNK_TOKEN env;
                        base URL from SPLUNK_HOST). Use `splunk-sdk-python`
                        if it's lighter than rolling raw requests; otherwise
                        `requests`.
    snapshot.py       — `fetch_snapshot(control_id, time_window)` →
                        returns a dict: {search, sample_events,
                        event_count, time_range}. Caches to local JSON
                        under .aec_cache/ so repeated demo runs are
                        deterministic.
    spl_validator.py  — `run_spl(query, time_window)` for the adversary's
                        recommended_additional_searches. Returns hit
                        count + sample. Catches SPL parse errors and
                        returns them as evidence ("syntax error in
                        line 3 of query").

cli/aec_demo.py       — entry point. Args:
                          --control CC6.1 | --control PR.AC-1 (NIST) | ...
                          --window 30d (default)
                          --sample soc2-cc61 (uses canned snapshot,
                                              bypasses live Splunk)
                          --no-llm (skips panel, just shows snapshot —
                                    for debugging)
                        Output:
                          - Rich TUI of the panel debate (existing)
                          - out/transcript_<ts>.md (existing, from task 009)
                          - out/audit_memo_<ts>.md (NEW — human-readable
                            audit memo synthesizing the panel's verdict)

samples/
    soc2-cc61.json    — pre-canned Splunk snapshot (MFA evidence)
    soc2-cc72.json    — pre-canned (incident response evidence)
    iso27001-a921.json — pre-canned (access management evidence)
                        Each file shape matches `fetch_snapshot()`'s
                        return type so the canned and live paths are
                        interchangeable.
```

## Files to modify

```
src/aec/agent/panel.py
    + run_panel(...) accepts a new optional kwarg: `splunk_snapshot: dict`
    + when present, injects "Snapshot:\n<json>" into each persona's
      user message (after the existing fixture content; before the
      "produce JSON" instruction)
    + after consensus, if Adversary's recommended_additional_searches
      is non-empty AND `splunk_client` is provided AND
      AEC_RUN_ADVERSARY_SEARCHES=true (default false), executes each
      via spl_validator and includes results in the transcript under a
      new "## Adversary follow-up searches" section.

pyproject.toml
    + adds the lighter of: splunk-sdk OR requests (pick whichever is
      already pulled in transitively, or `requests` if neither)
    + adds optional [splunk] extras if splunk-sdk is heavy enough to
      gate behind opt-in install

README.md
    + Replace any "Splunk MCP" claim with current truth: "REST API
      with token auth today; MCP integration is future work."
    + Architecture diagram (mermaid): Splunk → snapshot → panel →
      transcript + memo
    + Quick-start: `pip install -e .[splunk] && AEC_SAMPLE=soc2-cc61
      aec_demo` — 30 seconds, zero Splunk required.
    + "Bring your own Splunk" section: SPLUNK_HOST + SPLUNK_TOKEN env
      vars; one example SPL search; link to docs/splunk-setup.md.

docs/splunk-setup.md
    + How to provision a token in Splunk Enterprise / Splunk Cloud
    + Required permissions for the token (`search`)
    + Test command: `python -m aec.splunk.client --probe`
```

## Tests to add

```
tests/test_splunk_client.py
    — mock SPLUNK_HOST responses via `responses` library or `httpx-mock`
      (whichever is lighter; or stdlib `unittest.mock`)
    — verify auth header set correctly
    — verify query encoding (newlines, special chars)
    — verify error handling (401, 503, timeout)
    — verify base URL respects scheme (http/https)

tests/test_snapshot.py
    — fetch_snapshot returns expected dict shape
    — cache hit returns from disk without HTTP call
    — cache miss writes to .aec_cache/<control_id>_<window>.json
    — corrupted cache → fallback to live fetch + overwrites cache

tests/test_spl_validator.py
    — valid SPL → returns {hit_count, sample, ok: true}
    — SPL with syntax error → returns {ok: false, error: <msg>}
    — SPL hitting allowlist violation (if any) → returns error
    — empty result → {hit_count: 0, sample: [], ok: true}

tests/test_panel_with_splunk.py
    — end-to-end: load samples/soc2-cc61.json → run_panel with
      splunk_snapshot kwarg → transcript contains the snapshot's
      event_count + at least one sample event field
    — when AEC_RUN_ADVERSARY_SEARCHES=true + mock spl_validator,
      transcript includes "Adversary follow-up searches" section
    — when AEC_RUN_ADVERSARY_SEARCHES=false (default), no follow-up
      section even if adversary recommended searches
```

**Mocking guidance.** Mock the Splunk SDK / `requests` at the *client boundary*, NOT inside `snapshot.py`. Don't write tests that mock our own internals — that's the AccessQuint audit lesson. The boundary is whichever HTTP library does the actual network call.

---

## Sample snapshot file shape

Each `samples/*.json` matches `fetch_snapshot()`'s return shape so the canned + live paths are interchangeable:

```json
{
  "control_id": "CC6.1",
  "framework": "SOC2",
  "snapshot_name": "soc2-cc61",
  "fetched_at": "2026-05-24T12:00:00Z",
  "time_range": { "earliest": "-30d", "latest": "now" },
  "search": "index=auth EventCode=4625 OR EventCode=4624 | stats count by user, EventCode",
  "event_count": 1247,
  "sample_events": [
    { "_time": "2026-05-23T08:14:00Z", "user": "alice", "EventCode": 4624, "src_ip": "10.0.1.42" },
    { "_time": "2026-05-23T09:01:00Z", "user": "bob",   "EventCode": 4625, "src_ip": "10.0.1.91" }
  ],
  "aggregations": {
    "successful_logins": 1198,
    "failed_logins": 49,
    "mfa_enforced_pct": 0.83
  }
}
```

The Adversary persona uses `aggregations.mfa_enforced_pct=0.83` to argue "17% of logins bypass MFA — INSUFFICIENT or FAIL on CC6.1." That's the demo's audit moment.

---

## Constraints (hackathon edition)

- **One new dependency max** — either `splunk-sdk` OR `requests`. Pick the lighter one. Gate behind `pip install -e .[splunk]` extras if heavy.
- **Total LOC budget ~800–1000.** Watch transport/client bloat.
- **Demo must run <30 seconds** from `aec_demo --sample soc2-cc61`.
- **Sample files are the primary judge experience.** Most judges won't have a Splunk instance. The canned snapshots ARE the demo.
- **No breaking changes** to `run_panel()` signature — `splunk_snapshot` is an optional kwarg with default `None`.

## Definition of done

- `aec_demo --sample soc2-cc61` produces:
  - Rich TUI of the panel debate
  - `out/transcript_<ts>.md`
  - `out/audit_memo_<ts>.md`
  - Total runtime <30s
- All 4 new test files pass
- README updated with current Splunk transport reality + quickstart + architecture
- Three sample files (`soc2-cc61.json`, `soc2-cc72.json`, `iso27001-a921.json`) committed
- `docs/splunk-setup.md` covers token provisioning + one example SPL

## Out of scope — do NOT do these in PR #4

- **Don't** build all of: Splunk Cloud auth + Splunk Enterprise auth + Splunk MCP. Pick REST + token. Document the others as "future work."
- **Don't** build a web UI. CLI + TUI + file artifacts is the demo.
- **Don't** add a database. Snapshots = JSON files in `samples/`; transcripts = markdown in `out/`. State stays on disk.
- **Don't** support every control framework. SOC 2 + ISO 27001 + NIST CSF (one sample each, three total) is enough. Cross-framework mappings are future work.
- **Don't** wire LangGraph, MCP, or AI Gateway. The panel runs as-is.

## Demo cue

`aec_demo --sample soc2-cc61` should look like:

```
$ aec_demo --sample soc2-cc61
[1/4] Loading snapshot: samples/soc2-cc61.json (1247 events, 30d window)
[2/4] Running panel debate (3 personas, parallel)…
      [Rich TUI: 3 columns, real-time persona reasoning streams]
[3/4] Consensus: FAIL — adversary surfaced 17% MFA bypass, both
      auditor and engineer downgrade
[4/4] Wrote out/transcript_2026-05-24T144320Z.md
      Wrote out/audit_memo_2026-05-24T144320Z.md
Done in 23s.
```

That `23s` line is what wins the hackathon.

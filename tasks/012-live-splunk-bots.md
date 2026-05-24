# Task 012 — Live Splunk Enterprise + BOTS v3 + first live control

**Goal:** Stop being a hackathon submission with only canned samples. Stand up real Splunk on the VM, ingest BOTS v3 sample data, and execute *at least one* live SPL search through the existing `aec_demo` flow with no `--sample` flag.

**Budget:** ~200 LOC of code (mostly fetch_snapshot updates) + a runbook. The Splunk install itself is operator work, not code.

## Two-part task

### Part A — Splunk Enterprise + BOTS v3 install (operator-driven)

Document in `docs/splunk-setup.md`. The YC implementer drafts the runbook; the operator (Veera) runs it manually on the VM. Steps:

1. **Docker pull:** `docker run -d --name splunk -p 8000:8000 -p 8088:8088 -p 8089:8089 \
     -e SPLUNK_START_ARGS="--accept-license" \
     -e SPLUNK_PASSWORD="<chosen-pwd>" \
     splunk/splunk:latest`
2. **Wait for ready:** `until docker exec splunk /opt/splunk/bin/splunk status 2>/dev/null | grep -q running; do sleep 5; done`
3. **Download BOTS v3:** Detailed steps in the runbook. The dataset is ~30GB. (Reference: https://github.com/splunk/botsv3.)
4. **Index BOTS v3:** mount the data, configure inputs, monitor index size.
5. **Create an API token:** `Settings → Tokens → New Token`. Set `SPLUNK_TOKEN` in `.env`.
6. **Probe:** `python -m aec.splunk.client --probe` should return `{ "ok": true, "version": "...", "indexes": ["botsv3", ...] }`.

The runbook is the operator-facing acceptance criterion. The YC implementer writes it but doesn't execute it.

### Part B — Code changes (YC-implementer-driven)

1. **`src/aec/splunk/client.py`:** Add `--probe` CLI mode that verifies auth + lists indexes + reports BOTS v3 sourcetypes (`wineventlog`, `aws_cloudtrail`, `o365`, `iis`, `stream:dns`).

2. **`src/aec/splunk/snapshot.py`:** Update `fetch_snapshot(control_id, time_window, live=True)`:
   - When `live=True`: run the actual SPL via the Splunk client.
   - When `live=False`: fall back to `samples/<control_id>.json`.
   - Cache live results to `.aec_cache/<control_id>_<window>_<sha8>.json` so repeated demo runs are deterministic without re-querying.

3. **`cli/aec_demo.py`:** Drop the `--sample` *requirement*. New behavior:
   - If `--sample <name>` provided → use that canned file.
   - Else if `SPLUNK_HOST` + `SPLUNK_TOKEN` present → live fetch.
   - Else → error with actionable message.

4. **First live control — SOC 2 CC6.1.c (MFA bypass):**
   - SPL: `index=botsv3 sourcetype=o365:management:activity action=Login | stats count by user, mfa_used, src_ip | where mfa_used="false"`
   - Run via `aec_demo --control CC6.1` (no `--sample`)
   - Verify the Adversary persona surfaces real users (not the canned ones) from BOTS v3 data.

5. **Pre-canned samples regenerate from live data:**
   - `samples/soc2-cc61.json` re-built from live BOTS v3 (replaces the synthetic data 010 shipped)
   - Same for `samples/soc2-cc72.json` and `samples/iso27001-a921.json`
   - These now contain *real* event signatures + counts that match what a fresh BOTS v3 install would produce

## Tests

- `tests/test_splunk_client.py` — gain a `@pytest.mark.integration` test that hits localhost Splunk, gated on `SPLUNK_LIVE_TEST=1`. Skips by default.
- `tests/test_snapshot.py` — verify `live=True` vs `live=False` paths both return correct shape. Mock the HTTP boundary for `live=True`.

## Constraints

- BOTS v3 is large (~30GB). VM disk check: `df -h ~` should show ≥ 40GB free before download. If not, blocker the operator.
- Splunk container takes ~3 min to fully start (license accept + index init).
- Splunk Enterprise free trial = 500MB/day indexing limit. BOTS v3 ingestion fits in the historic-data path (uses `oneshot` upload, doesn't count against daily quota).

## Definition of done

- `docs/splunk-setup.md` runbook exists; operator confirms it runs cleanly end-to-end.
- `python -m aec.splunk.client --probe` returns the expected indexes/sourcetypes.
- `aec_demo --control CC6.1` (no `--sample`) runs against live Splunk, produces all 4 artifacts, in <60s (live SPL is slower than canned).
- `samples/` files regenerated from live data and committed.
- Architecture diagram updated to show live Splunk path is now the default.

## Demo cue

In the demo video, this is the "this isn't a mock" beat:

```
$ aec_demo --control CC6.1 --live
[1/5] Connecting to Splunk (localhost:8089)... ✓ (BOTS v3 index, 1.2M events)
[2/5] Generating SPL for SOC 2 CC6.1.c — MFA enforcement
[3/5] Executing SPL via REST API (1,247 events returned, 23s)
[4/5] Running panel debate (3 personas, parallel)...
      [Rich TUI: 3 columns, real-time persona reasoning]
[5/5] Consensus: FAIL — 12 service accounts confirmed bypassing MFA

Wrote out/audit_memo_<ts>.md
Wrote out/transcript_<ts>.md
Wrote out/audit_trail_<ts>.jsonl  (4 snapshots, SHA-256 chained)
Wrote out/gap_report_<ts>.xlsx  (Merkle-sealed)

Done in 58s.
```

Switch to Splunk Web UI in the video at the SPL step — show the same query running natively. That's the "this is real" moment for the judge.

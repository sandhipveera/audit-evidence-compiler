# Task 023 — Auditor Verification Portal

**Goal:** A public page at `https://aec.accessquint.com/verify` where an external auditor uploads `audit_trail.jsonl` and instantly sees a visual chain-of-custody report — which snapshots are intact, when each was collected, which AI model produced it, and a single VERIFIED / TAMPERED banner. No install required.

**Budget:** ~80 LOC Python + ~60 LOC HTML/JS. ~1 day. Highest ROI addition remaining.

## Why this closes the trust story completely

Every other AI compliance tool generates text. Nobody can verify whether the AI-generated findings were modified after the fact. This page is the answer to the question every auditor will ask:

> "How do I know this evidence hasn't been tampered with since collection?"

They upload the file. They get a cryptographic answer. That's the product.

## What it shows

```
┌─────────────────────────────────────────────────────────────┐
│  ✓  AUDIT TRAIL VERIFIED — 5 of 5 snapshots intact          │
│     No modifications detected since collection               │
├─────────────────────────────────────────────────────────────┤
│  Run ID:    a3f8c2d1-...                                     │
│  Collected: 2026-05-25T20:14:32Z                            │
│  Control:   SOC 2 CC6.1 — MFA Enforcement                   │
│  Verdict:   FAIL (consensus, 4 vendors)                      │
├─────────────────────────────────────────────────────────────┤
│  Snapshot 1  ✓  Auditor (Claude Sonnet 4)       PARTIAL      │
│  Snapshot 2  ✓  Engineer (GPT-5.5)              FAIL         │
│  Snapshot 3  ✓  Adversary (Gemini 2.5 Pro)      FAIL         │
│  Snapshot 4  ✓  Security Model (Foundation-Sec) FAIL         │
│  Snapshot 5  ✓  Consensus + Merkle root                      │
├─────────────────────────────────────────────────────────────┤
│  Chain root: sha256:4a7f3b...                                │
│  [Download verification certificate]                         │
└─────────────────────────────────────────────────────────────┘
```

If any snapshot was modified: red TAMPERED banner, shows which snapshot number failed, shows expected vs actual hash.

## Implementation

### Server side (web/main.py addition, ~50 LOC)

```python
@app.get("/verify")
def verify_page():
    return FileResponse("web/static/verify.html")

@app.post("/api/verify")
async def verify_trail(file: UploadFile = File(...)):
    """Accept audit_trail.jsonl, run verify_chain(), return structured result."""
    content = await file.read()
    try:
        snapshots = [json.loads(line) for line in content.decode().strip().splitlines() if line]
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return JSONResponse({"ok": False, "error": f"Invalid file: {e}"}, status_code=400)

    from aec.integrity.chain import verify_chain
    result = verify_chain(snapshots)   # already exists — returns VerificationResult

    return {
        "ok": result.valid,
        "total": result.total,
        "verified": result.verified,
        "failed_at": result.failed_at,        # None if all pass
        "chain_root": result.chain_root,
        "run_id": snapshots[0].get("run_id") if snapshots else None,
        "control_id": snapshots[0].get("control_id") if snapshots else None,
        "collected_at": snapshots[0].get("timestamp") if snapshots else None,
        "snapshots": [
            {
                "index": i + 1,
                "ok": result.snapshot_results[i],
                "persona": s.get("persona"),
                "verdict": s.get("verdict"),
                "transport": s.get("transport"),
                "timestamp": s.get("timestamp"),
                "hash": s.get("this_hash", "")[:16] + "...",
            }
            for i, s in enumerate(snapshots)
        ],
    }
```

### Frontend (web/static/verify.html, ~60 LOC)

Simple drag-and-drop upload page. On submit, POST to `/api/verify`, render result.

- VERIFIED: green banner, table of snapshots with green checkmarks
- TAMPERED: red banner, highlights which snapshot failed, shows hash mismatch
- "Download verification certificate" button: renders a simple text cert with the result + timestamp

No framework. Vanilla JS, same style as index.html.

## Verification certificate (downloadable)

```
AUDIT EVIDENCE VERIFICATION CERTIFICATE
========================================
Generated:   2026-05-25T21:33:10Z
Portal:      https://aec.accessquint.com/verify

Result:      VERIFIED ✓
Run ID:      a3f8c2d1-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Control:     SOC 2 CC6.1 — MFA Enforcement
Collected:   2026-05-25T20:14:32Z
Verdict:     FAIL (4-vendor consensus)

Chain root:  sha256:4a7f3b9c...
Snapshots:   5 of 5 verified intact

This certificate confirms that the audit_trail.jsonl file
submitted to aec.accessquint.com/verify on 2026-05-25T21:33:10Z
passed SHA-256 Merkle chain verification. No modifications were
detected between evidence collection and verification.

Verification is cryptographic. Any post-collection edit to any
snapshot changes its hash and breaks the chain.
========================================
```

## Files to create / modify

- `web/main.py` — add `/verify` GET + `/api/verify` POST (~50 LOC)
- `web/static/verify.html` — drag-and-drop upload + result display (~60 LOC)
- Update `web/static/index.html` — add "Verify an audit trail →" link in header/footer

## Definition of done

- `https://aec.accessquint.com/verify` loads a working upload page
- Uploading a valid `audit_trail.jsonl` shows green VERIFIED banner + per-snapshot table
- Uploading a tampered file (any hash changed) shows red TAMPERED banner + which snapshot
- "Download certificate" button produces a text file
- No auth required — public page, no rate limit (file upload only, no LLM calls)

## Demo cue (15 seconds — strongest trust moment in the video)

Switch to browser. Open `/verify`. Drag the `audit_trail.jsonl` onto the page.

Watch the green banner appear: "5 of 5 snapshots verified — chain intact."

Voice-over: "Any external auditor can verify the evidence chain. No install. Upload the file, get a cryptographic proof. This is AI-generated compliance evidence you can stake your reputation on."

Then drag a *modified* file (edit one line in the jsonl). Red banner: "TAMPERED — snapshot 3 hash mismatch." One sentence says it all.

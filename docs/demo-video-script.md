# 3-Minute Demo Video Script
## Audit Evidence Auto-Compiler — Splunk Agentic Ops Hackathon 2026

**Total runtime:** 3:00 (with natural pauses)  
**Recording setup:** tmux 3-pane split on left + browser on right (or two monitors)  
**Font size:** 18pt minimum — judges watch on laptop screens  
**Resolution:** 1920×1080

---

## Pre-recording checklist

```bash
# On the VM — ensure Splunk + MCP servers are running
docker ps | grep -E "splunk|mcp"

# Clear previous output so artifacts are fresh
rm -f ~/audit-evidence-compiler/out/*

# Ensure OAuth is fresh
claude --print '{"role":"user","content":"ping"}' 2>/dev/null | head -1

# Pre-warm the terminal (looks cleaner on video)
cd ~/audit-evidence-compiler
clear
```

Open browser to `https://aec.accessquint.com` — have it ready but minimized.

---

## SHOT 1 — The problem (0:00–0:20)

**[Terminal, full screen]**

Type slowly, don't hit Enter yet:
```
aec_demo --sample soc2-cc61
```

**Voice-over:**
> "A SOC 2 audit cycle takes a vCISO 40 hours. Most of that is pulling evidence from Splunk,
> formatting it into xlsx, writing the rationale. This agent does it in one command."

Hit Enter.

---

## SHOT 2 — The panel debate runs (0:20–1:10)

**[Watch the Rich TUI render — 3 columns appear]**

Let it run. Don't narrate over the persona reasoning streaming in — let the judges read it.

When all three verdicts appear, **voice-over:**
> "Three vendors. Claude plays the Auditor — reads the control language literally.
> GPT plays the Engineer — checks the SPL for statistical soundness.
> Gemini plays the Adversary — tries to disprove the PASS verdict."

When consensus line appears (`Consensus → FAIL`):
> "Lowest verdict wins. One dissenting voice is enough to force a FAIL.
> The full debate transcript ships with the report."

---

## SHOT 3 — The Adversary's counter-search changes the verdict (1:10–1:45)

**[Scroll up in terminal to show Round 2 section of transcript, OR run live]**

If running live with recurrence enabled, the terminal will already show:
```
[Counter-evidence] Adversary recommended 1 follow-up search
[MCP] Executing: index=botsv3 | stats count by user, mfa_used, src_ip
      → 1,102 events — 3 service accounts: 0 MFA events in 90 days

Round 2 panel debate...
Adversary → FAIL (new evidence: privileged accounts bypassing MFA entirely)
Final consensus: FAIL
```

**Voice-over:**
> "The Adversary doesn't just critique — it proposes counter-searches.
> Those execute automatically against live Splunk.
> Round 2 runs with the new evidence. The verdict can change."

---

## SHOT 4 — The artifacts (1:45–2:00)

**[ls out/ in terminal]**

```bash
ls -lh out/
```

Shows:
```
gap_report_2026-05-25T....xlsx       (Merkle-sealed, 6 findings)
transcript_2026-05-25T....md         (full 3-vendor debate)
audit_memo_2026-05-25T....md         (executive summary)
audit_trail_2026-05-25T....jsonl     (tamper-evident chain)
```

**Voice-over:**
> "Four artifacts. The xlsx matches the Audit Findings Remediation Tracker format
> that real audit committees already use. The jsonl chain is SHA-256-sealed —
> any post-collection edit shows up immediately."

Run verification:
```bash
aec verify out/gap_report_*.xlsx --trail out/audit_trail_*.jsonl
```

Shows 5 green checkmarks.

> "Tamper-evident in under 2 seconds."

---

## SHOT 5 — Inside Splunk (2:00–2:25)

**[Switch to Splunk Web — open browser, navigate to search bar]**

Type in Splunk search bar:
```
| inputlookup admin_logins.csv | auditcompiler control=CC6.1 mode=summary
```

Hit search. Results table populates with `verdict`, `severity`, `root_cause` columns.

**Voice-over:**
> "Every other entry calls Splunk from the outside.
> This one is a search command registered inside Splunk's query pipeline.
> You type auditcompiler in any search — three vendors debate — results appear as columns."

---

## SHOT 6 — The live dashboard (2:25–2:50)

**[Switch to browser — open https://aec.accessquint.com]**

Select "SOC 2 CC6.1 — MFA enforcement" from dropdown. Click **Run debate**.

Watch the three persona columns stream live.

**Voice-over:**
> "Judges can run this themselves. No clone, no install, no setup.
> Just a URL."

Let consensus verdict appear on screen. Pause 3 seconds.

---

## SHOT 7 — The agent caught its own bug (2:50–3:00)

**[Terminal — cat the gold transcript]**

```bash
grep -A3 "earliest" out/2026-05-25T022914Z.md | head -10
```

Or just show the relevant section:
```
Auditor note: The time window uses `-30d` relative dates.
BOTS v3 data is from 2018. This query will return zero events.
Recommended fix: earliest=2018-08-01 latest=2018-09-30
```

**Voice-over:**
> "And this — mid-debate — the Auditor caught its own setup bug.
> Wrong time window against a 2018 dataset. It recommended the fix.
> We accepted it. The follow-up run returned 1,247 events."

**[End on terminal showing the corrected run completing]**

---

## Recording tips

- **Do one full dry run** before recording. The panel takes ~28s — know exactly when to talk.
- **Silence during persona reasoning** — let judges read the debate. Your voice competes with the text.
- **Zoom in** on the consensus line and the artifact list — those are the money shots.
- **Use `script` or `asciinema`** for a clean terminal recording, then composite with browser via OBS.
- **Trim aggressively** — if a take runs to 3:10, cut the problem intro (Shot 1) to 10 seconds.

## OBS scene setup (if using OBS)

- Scene 1: Terminal full-screen (Shots 1–4)
- Scene 2: Browser split — Splunk Web left, terminal right (Shot 5)
- Scene 3: Browser full-screen — `aec.accessquint.com` (Shot 6)
- Scene 4: Terminal full-screen — gold transcript (Shot 7)

Hotkeys: F1–F4 for scene switches. Practice the switches so they're clean on camera.

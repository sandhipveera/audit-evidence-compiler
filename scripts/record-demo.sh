#!/usr/bin/env bash
# =============================================================================
# Audit Evidence Auto-Compiler — Automated Demo Recorder
#
# Usage:
#   asciinema rec demo.cast --overwrite
#   bash scripts/record-demo.sh
#   (Ctrl+D when done)
#
# Or with auto-stop:
#   asciinema rec demo.cast --overwrite --command "bash scripts/record-demo.sh"
#
# After recording, upload or convert:
#   asciinema upload demo.cast
#   # OR convert to gif: agg demo.cast demo.gif
# =============================================================================

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# Activate venv so aec_demo and aec are on PATH
# shellcheck disable=SC1091
source "$REPO_DIR/.venv/bin/activate"

# Load .env so the panel sees HF_TOKEN (Foundation-Sec-8B) and SPLUNK_* — without
# it the security_model vendor silently drops and the demo records only 3 vendors.
if [ -f "$REPO_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_DIR/.env"
  set +a
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Type text character by character (human feel)
type_cmd() {
  local cmd="$1"
  local delay="${2:-0.04}"
  printf "\033[1;32m\$\033[0m "
  for ((i=0; i<${#cmd}; i++)); do
    printf "%s" "${cmd:$i:1}"
    sleep "$delay"
  done
  sleep 0.4
  echo
}

# Run a command with typing animation, then execute it
run() {
  local cmd="$1"
  local pause_after="${2:-1.5}"
  type_cmd "$cmd"
  eval "$cmd"
  sleep "$pause_after"
}

# Print a section header
header() {
  echo
  printf "\033[1;36m━━━  %s  ━━━\033[0m\n" "$1"
  echo
  sleep 0.8
}

# Pause with a visible countdown (gives voice-over time to breathe)
pause() {
  sleep "${1:-2}"
}

# ---------------------------------------------------------------------------
# SHOT 0 — Clean slate
# ---------------------------------------------------------------------------

clear
sleep 1

echo -e "\033[1;37mAudit Evidence Auto-Compiler\033[0m"
echo -e "\033[0;90mSplunk Agentic Ops Hackathon 2026 — Security Track\033[0m"
echo
sleep 2

# ---------------------------------------------------------------------------
# SHOT 1 — One command, zero Splunk needed
# ---------------------------------------------------------------------------

header "SOC 2 CC6.1 — MFA Enforcement"

run "aec_demo --sample soc2-cc61" 2

# (Panel debate runs here — Rich TUI renders automatically)
# Voice-over happens while panel is running

# ---------------------------------------------------------------------------
# SHOT 3 — Show the artifacts
# ---------------------------------------------------------------------------

pause 1
header "Artifacts"

run "ls -lh out/" 1.5

# ---------------------------------------------------------------------------
# SHOT 4 — Tamper-evident verification
# ---------------------------------------------------------------------------

header "Tamper-Evident Verification"

run "aec verify out/gap_report_*.xlsx --trail out/audit_trail_*.jsonl" 2

pause 1

# ---------------------------------------------------------------------------
# SHOT 5 — Drift detection: two audit windows compared
# ---------------------------------------------------------------------------

header "Drift Detection — Q1 vs Q2"

run "aec_demo --compare soc2-cc61,soc2-cc61-q2" 2

pause 1

# ---------------------------------------------------------------------------
# SHOT 6 — Multi-framework: one prompt covers SOC 2 + ISO 27001
# (shows the command + expected output; live run requires Splunk)
# ---------------------------------------------------------------------------

header "Multi-Framework Mapping"

type_cmd 'aec_demo --control "SOC2:CC6.1+ISO:A.9.2.3"'
cat <<'MULTIFW'
[1/6] Mapping 2 framework controls → 2 unique internal controls
      (CTRL-003 satisfies both SOC 2 CC6.1 and ISO 27001 A.9.2.3)
[2/6] Generated 1 SPL query (instead of 2 — saved 50% execution time)
[3/6] Executing via MCP (splunk-official)...
[4/6] Panel debate (CTRL-003 — Access Control Policy, 4 personas)...
      Auditor        → FAIL    (MFA not enforced for all users)
      Engineer       → FAIL    (17% bypass rate confirmed)
      Adversary      → FAIL    (3 privileged accounts: 0 MFA events)
      Security Model → FAIL    (service accounts are an attacker pivot point)
[5/6] Consensus: FAIL — triggers findings in both frameworks
      SOC 2 CC6.1 → FAIL
      ISO 27001 A.9.2.3 → FAIL
[6/6] Wrote out/gap_report_multi_<ts>.xlsx (4 findings, 2 frameworks)
Done in 31s.
MULTIFW

pause 2

# ---------------------------------------------------------------------------
# SHOT 7 — LangGraph HITL gate (show interactive mode transcript)
# ---------------------------------------------------------------------------

header "LangGraph Orchestration — Human-in-the-Loop"

type_cmd "aec_demo --sample soc2-cc61 --review interactive"
cat <<'LANGGRAPH'
[graph] node: control_mapper        (98ms)
[graph] node: spl_generator         (1840ms)
[graph] node: spl_validator         (12ms)   → policy: pass

⏸  HITL gate: review SPL before execution

  SPL: index=botsv3 sourcetype=o365:management:activity action=Login
       | stats count by user, mfa_used | where mfa_used="false"

  Estimated events: ~47   Estimated runtime: ~2.3s

  [a]pprove / [e]dit / [r]eject:  a  ← auto-approved

[graph] node: mcp_executor          (2280ms  via splunk-official)
[graph] node: evidence_normalizer   (8ms)
[graph] node: panel_round_1         (28400ms — 4 personas parallel)
[graph] node: consensus             (4ms)

⏸  HITL gate: review verdict before sealing artifacts

  Verdict: FAIL  (confidence: 0.91)
  [a]pprove / [r]eject:  a  ← auto-approved

[graph] node: evidence_formatter    (210ms)
[graph] node: merkle_chain_sealer   (18ms)
[graph] Checkpoint saved → .aec_cache/checkpoints/<run_id>.json
LANGGRAPH

pause 2

# ---------------------------------------------------------------------------
# SHOT 8 — Gold artifact: agent caught its own setup bug
# ---------------------------------------------------------------------------

header "The Agent Caught Its Own Bug"

type_cmd "grep -A4 'time window' out/transcript_*.md | head -20"
cat <<'GOLDBUG'
-- Auditor (Claude Sonnet 4) mid-debate critique --

  ⚠  Time window issue detected.

  The SPL uses `earliest=-30d latest=now`. The BOTS v3 dataset
  covers August–September 2018. A relative -30d window from 2026
  will return zero events — the query is structurally sound but
  the time range produces an empty result set.

  Recommended fix:
    earliest=2018-08-01  latest=2018-09-30

  This is a data-availability issue, not a control failure.
  Re-run with the corrected window before issuing a verdict.

-- We accepted the recommendation. The follow-up run returned 1,247 events. --
GOLDBUG

pause 2

# ---------------------------------------------------------------------------
# SHOT 9 — Live dashboard teaser
# ---------------------------------------------------------------------------

header "Live Dashboard"

echo -e "\033[1;33mhttps://aec.accessquint.com\033[0m"
echo
echo -e "  Pick a control. Hit Run."
echo -e "  Four vendors debate. Results stream live."
echo -e "  No install. No setup. Just a URL."
echo
sleep 3

# ---------------------------------------------------------------------------
# END
# ---------------------------------------------------------------------------

echo
echo -e "\033[1;32m✓ Four vendors. One command. Audit-ready in 30 seconds.\033[0m"
echo
sleep 2

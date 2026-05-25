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
# ---------------------------------------------------------------------------

header "Multi-Framework Mapping"

run 'aec_demo --control "SOC2:CC6.1+ISO:A.9.2.3"' 2

pause 1

# ---------------------------------------------------------------------------
# SHOT 7 — LangGraph HITL gate (auto mode to avoid blocking)
# ---------------------------------------------------------------------------

header "LangGraph Orchestration"

run "aec_demo --sample soc2-cc61 --review auto 2>&1 | grep -E 'node:|HITL|graph|checkpoint'" 2

pause 1

# ---------------------------------------------------------------------------
# SHOT 8 — Gold artifact: agent caught its own setup bug
# ---------------------------------------------------------------------------

header "The Agent Caught Its Own Bug"

# Show the key lines from the gold transcript
run "grep -A4 'time window\|earliest\|BOTS v3\|2018' out/2026-05-25T022914Z.md 2>/dev/null | head -20 || \
     grep -A4 'time window\|earliest\|2018' out/transcript_*.md 2>/dev/null | head -20" 2

pause 1

# ---------------------------------------------------------------------------
# SHOT 9 — Live dashboard teaser
# ---------------------------------------------------------------------------

header "Live Dashboard"

echo -e "\033[1;33mhttps://aec.accessquint.com\033[0m"
echo
echo -e "  Pick a control. Hit Run."
echo -e "  Three vendors debate. Results stream live."
echo -e "  No install. No setup. Just a URL."
echo
sleep 3

# ---------------------------------------------------------------------------
# END
# ---------------------------------------------------------------------------

echo
echo -e "\033[1;32m✓ Three vendors. One command. Audit-ready in 30 seconds.\033[0m"
echo
sleep 2

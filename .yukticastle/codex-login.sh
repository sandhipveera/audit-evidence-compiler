#!/usr/bin/env bash
# codex-login.sh — refresh the host's ~/.codex/auth.json access_token
# used by YuktiCastle's free ChatGPT-billed codex-reviewer path.
#
# Why this exists: yukticastle's main.mts reads `~/.codex/auth.json` at
# launch and forwards the content into the reviewer container when the
# reviewer is codex-backed. The access_token has a ~1h life. If it's
# stale at orchestrator start, the container's codex CLI auto-refreshes
# from the refresh_token transparently — so this script is rarely
# strictly necessary. It's here as a:
#
#   - Pre-flight: refresh BEFORE a long Phase 2 so the container starts
#     with a long expiry runway (~1h from now).
#   - Diagnostic: confirms host's codex CLI is signed in + working.
#
# Same mechanism as claude-login.sh: send a one-token prompt through
# `codex exec` (non-interactive). The CLI's startup flow:
#   1. reads ~/.codex/auth.json
#   2. notices the access_token is expired
#   3. uses the refresh_token to mint a new access_token
#   4. writes the new tokens back to ~/.codex/auth.json
#   5. answers the prompt
#
# When the token is already fresh, this is a near-no-op. When expired,
# the refresh happens transparently and the next yukticastle run starts
# with a maximum-life token.
#
# Prereqs: codex CLI installed (`@openai/codex`), previously signed in
# via `codex login` at least once.

set -uo pipefail

# Force the CLI to use the chatgpt auth path, not OPENAI_API_KEY.
unset OPENAI_API_KEY

if ! command -v codex >/dev/null 2>&1; then
    echo "[codex:login] ✗ \`codex\` CLI not on PATH. Install with:"
    echo "    npm install -g @openai/codex"
    exit 127
fi

if [[ ! -f "$HOME/.codex/auth.json" ]]; then
    echo "[codex:login] ✗ ~/.codex/auth.json not found. Sign in interactively first:"
    echo "    codex login            # opens browser for ChatGPT auth"
    exit 1
fi

echo "[codex:login] triggering token refresh via tiny prompt..."

# `codex exec` is the non-interactive subcommand. --skip-git-repo-check
# skips the "are you in a git repo" guard (we may be running anywhere).
# Redirect stdout to /dev/null but keep stderr visible for diagnostics.
if ! codex exec --skip-git-repo-check 'say one word: pong' >/dev/null 2>&1; then
    STATUS=$?
    echo "[codex:login] ✗ codex exec exited $STATUS — is the CLI signed in?"
    echo "    Try \`codex login\` interactively first."
    exit "$STATUS"
fi

# Show remaining time on the new access_token. Pull `exp` from the JWT
# payload (base64url with right-pad fix). `jq` + `base64` may not exist
# in minimal environments; degrade gracefully to "(unable to introspect)"
# if either is missing.
REMAINING="(unable to introspect — install jq + python3 for token-expiry display)"
if command -v jq >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    JWT_PAYLOAD="$(jq -r '.tokens.access_token' "$HOME/.codex/auth.json" 2>/dev/null | awk -F. '{print $2}')"
    if [[ -n "$JWT_PAYLOAD" ]]; then
        EXP="$(printf '%s' "$JWT_PAYLOAD" | python3 -c 'import sys, base64, json; s=sys.stdin.read().strip(); s+="=" * (-len(s) % 4); print(json.loads(base64.urlsafe_b64decode(s)).get("exp", 0))' 2>/dev/null)"
        if [[ -n "$EXP" ]] && [[ "$EXP" -gt 0 ]]; then
            NOW="$(date +%s)"
            REMAINING_MIN=$(( (EXP - NOW) / 60 ))
            REMAINING="${REMAINING_MIN} min remaining"
        fi
    fi
fi

echo "[codex:login] ✓ done. Token state: $REMAINING"

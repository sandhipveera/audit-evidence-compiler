#!/usr/bin/env bash
# claude-login.sh — refresh the macOS Keychain OAuth token used by
# YuktiCastle's free MAX-billed run path.
#
# Why this exists: yukticastle's main.mts reads the OAuth token from
# the Keychain at launch. That token has a ~6h life. If you've not used
# `claude` on this host for that long, the keychain entry is stale and
# yukticastle falls back to ANTHROPIC_API_KEY (per-token billing).
#
# This script triggers a token refresh by sending a one-token prompt
# through the Claude CLI's non-interactive mode (`-p`). The CLI's
# startup auth flow:
#   1. reads the keychain
#   2. notices the access token is expired
#   3. uses the refresh token to mint a new access token
#   4. writes the new token back to the keychain
#   5. answers the prompt
#
# When the token is already fresh, this is a near-no-op (one trivial
# prompt that bills $0 against MAX). When expired, the refresh happens
# transparently and the next yukticastle run goes free.
#
# Prereqs: macOS, Claude Code CLI installed (`claude`), previously
# signed in at least once.

set -uo pipefail

# Force the CLI to use keychain auth, not any ANTHROPIC_API_KEY in env.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

if ! command -v claude >/dev/null 2>&1; then
    echo "[claude:login] ✗ \`claude\` CLI not on PATH. Install Claude Code first."
    exit 127
fi

echo "[claude:login] triggering keychain refresh via tiny prompt..."

if ! claude -p "say one word: pong" --max-turns 1 >/dev/null 2>&1; then
    STATUS=$?
    echo "[claude:login] ✗ claude exited $STATUS — is the CLI signed in? Try \`claude\` interactively first."
    exit "$STATUS"
fi

REMAINING="$(
    security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null \
        | jq -r '((.claudeAiOauth.expiresAt - (now * 1000)) / 60000 | floor | tostring) + " min remaining"' \
        2>/dev/null \
        || echo "(unable to read keychain)"
)"

echo "[claude:login] ✓ done. Token state: $REMAINING"

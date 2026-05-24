#!/usr/bin/env bash
# api-key.sh — manage a host-wide Anthropic API key stored in the
# macOS Keychain. YuktiCastle's main.mts reads this entry as the
# failover when OAuth is unavailable AND no ANTHROPIC_API_KEY env
# var is set, so you paste the key ONCE per host and every fresh
# clone / worktree picks it up automatically.
#
# Project-local ANTHROPIC_API_KEY (.yukticastle/.env or shell export)
# still wins — this layer is purely a fallback to remove per-checkout
# toil. Wired via:
#   npm run yukticastle:api-key:set    -> set | this script
#   npm run yukticastle:api-key:unset  -> unset
#   npm run yukticastle:api-key:peek   -> peek (shows first/last 4 chars)
#
# Service name (so you can poke at it directly via `security`):
#   yukticastle-anthropic-api-key
#
# Usage: bash .yukticastle/api-key.sh {set|unset|peek}

set -uo pipefail

SERVICE="yukticastle-anthropic-api-key"
ACCOUNT="${USER:-yukticastle}"

case "${1:-}" in
    set)
        if ! command -v security >/dev/null 2>&1; then
            echo "[yukticastle:api-key] ✗ \`security\` CLI not found — this helper is macOS-only."
            echo "  On Linux/CI, set ANTHROPIC_API_KEY in .yukticastle/.env instead."
            exit 127
        fi
        echo "[yukticastle:api-key] Storing Anthropic API key in macOS Keychain"
        echo "  service:  ${SERVICE}"
        echo "  account:  ${ACCOUNT}"
        echo ""
        echo "  You'll be prompted for the key (input hidden)."
        echo "  Tip: paste with ⌘V — the field accepts paste."
        echo ""
        # -U updates if entry exists; -w without arg prompts interactively
        # with hidden input. -T '' restricts access to the security
        # binary only (no GUI apps prompted on read; tightens scope).
        if security add-generic-password -U -s "${SERVICE}" -a "${ACCOUNT}" -w; then
            echo "[yukticastle:api-key] ✓ stored. Future `npm run agents:run` calls will pick this up automatically."
        else
            STATUS=$?
            echo "[yukticastle:api-key] ✗ security exited ${STATUS} — entry NOT updated."
            exit "${STATUS}"
        fi
        ;;
    unset|delete|remove)
        if security delete-generic-password -s "${SERVICE}" >/dev/null 2>&1; then
            echo "[yukticastle:api-key] ✓ Keychain entry removed (${SERVICE})."
        else
            echo "[yukticastle:api-key] ↷ no Keychain entry to remove (${SERVICE} not found)."
        fi
        ;;
    peek|show|status)
        if KEY="$(security find-generic-password -s "${SERVICE}" -w 2>/dev/null)"; then
            LEN="${#KEY}"
            if [ "${LEN}" -ge 8 ]; then
                PREFIX="${KEY:0:8}"
                SUFFIX="${KEY: -4}"
                echo "[yukticastle:api-key] ✓ Keychain entry present: ${PREFIX}…${SUFFIX} (${LEN} chars)"
                if [[ "${KEY}" != sk-ant-* ]]; then
                    echo "[yukticastle:api-key] ⚠  warning: key doesn't start with \"sk-ant-\" — main.mts will ignore it."
                fi
            else
                echo "[yukticastle:api-key] ⚠  Keychain entry present but suspiciously short (${LEN} chars)."
            fi
        else
            echo "[yukticastle:api-key] ↷ no Keychain entry (${SERVICE}). Run: npm run yukticastle:api-key:set"
        fi
        ;;
    *)
        cat <<EOF
Usage: bash .yukticastle/api-key.sh {set|unset|peek}

  set     Store an Anthropic API key in the macOS Keychain (one-time setup;
          survives across all checkouts and worktrees).
  unset   Remove the stored entry.
  peek    Show whether an entry exists, length, and prefix/suffix.

NPM wrappers:
  npm run yukticastle:api-key:set
  npm run yukticastle:api-key:unset
  npm run yukticastle:api-key:peek
EOF
        exit 2
        ;;
esac

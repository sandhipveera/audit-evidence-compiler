# YuktiCastle agent container — Claude Code + Codex (multi-agent verity build).
#
# Generated from @ai-hero/sandcastle@0.5.8 CLAUDE_CODE_DOCKERFILE and
# CODEX_DOCKERFILE templates, merged so the same image can host either
# agent (the orchestrator may swap reviewer between Claude Sonnet and
# Codex/GPT-5 over time).

FROM node:22-bookworm

# System deps
#
# Python toolchain (python3 + pip + venv) is included so agents working
# on projects with Python subtrees (trading executors, ML notebooks,
# automation scripts, ports from TS → Py) don't have to re-derive the
# `get-pip.py + --break-system-packages` bootstrap dance on every run.
# Before this was added, gextrader's first end-to-end run burned 12 + 9
# shell attempts (implementer + reviewer) just installing pytest. See
# FEEDBACK-gextrader-gex-engine-port.md Issue #1 + ENHANCEMENTS.md
# Tier 6 item #27.
#
# Debian Bookworm enforces PEP 668 (externally-managed-environment),
# which would normally block `pip install` outside a venv. We pass
# `--break-system-packages` because (a) this is a throwaway sandbox
# container, not a host distro install, and (b) creating a venv per
# project complicates the agent's pytest invocation.
#
# Pre-installed Python packages (~30MB total over the base
# Python install):
#   pytest         — test runner
#   pytest-httpx   — mock HTTPX inside pytest (works with pytest fixtures)
#   httpx          — modern HTTP client. gextrader's GextraderClient + Phase 1b
#                    test suite both import it transitively. Without it,
#                    every Python-task implementer hits ImportError and
#                    reinvents the `pip install httpx --break-system-packages`
#                    workaround. See
#                    FEEDBACK-gextrader-phase-1b-chain-endpoint.md Issue #3.
#   requests       — old-school HTTP client. Many older Python scripts
#                    still import it; ~free to include alongside httpx.
#   freezegun      — `freeze_time` decorator for deterministic time-based
#                    tests (cache TTLs, expiry, etc.).
#
# Total image-size cost is now ~80MB Python install + ~30MB packages
# ≈ 110MB. Roadmap follow-up: read project-local requirements.txt at
# runtime via a sandbox onSandboxReady hook (Option a from the Phase 1b
# feedback). Until then, this curated set covers the common cases
# without per-project Dockerfile diffs.
RUN apt-get update && apt-get install -y \
  git \
  curl \
  jq \
  python3 \
  python3-pip \
  python3-venv \
  && rm -rf /var/lib/apt/lists/* \
  && pip3 install --break-system-packages \
       pytest \
       pytest-httpx \
       httpx \
       requests \
       freezegun

# Rename node user → agent (UID 1000 preserved for the canonical layout).
# YuktiCastle runs the container as the HOST user's UID (process.getuid()
# on macOS is typically 501) so files written into the bind-mounted
# worktree are owned by the operator, not by an arbitrary container UID.
# This means /home/agent must be writable by ANY uid at runtime —
# `git config --global` etc. need to write to /home/agent/.gitconfig
# regardless of which UID the container runs as. world-writable home
# is fine in a sandbox container.
RUN usermod -d /home/agent -m -l agent node && \
    chmod -R a+rwX /home/agent && \
    chmod 1777 /home/agent

# Install Codex CLI globally (needs root) before dropping privileges
RUN npm install -g @openai/codex

USER agent

# Install Claude Code CLI as the agent user. The installer creates
# /home/agent/.claude/ owned by agent:node with 755 perms — those need
# to be relaxed AFTER the install so the runtime host UID can write
# state files (logs, projects/*.jsonl, sessions) Claude Code touches
# via appendFileSync. Without this, claude --print exits 0 silently
# with a swallowed ENOENT.
RUN curl -fsSL https://claude.ai/install.sh | bash

# Add Claude to PATH
ENV PATH="/home/agent/.local/bin:$PATH"

# Drop back to root for the post-install chmod sweep — agent (UID 1000)
# can chmod its own files but the host-UID runtime user (typically 501
# on macOS) needs world-writable access to anything Claude or Codex
# might touch.
USER root
RUN chmod -R 1777 /home/agent && \
    find /home/agent -type d -exec chmod 1777 {} + && \
    find /home/agent -type f -exec chmod a+rw {} +
USER agent

WORKDIR /home/agent

# In worktree sandbox mode, YuktiCastle bind-mounts the git worktree at
# /home/agent/workspace (SANDBOX_REPO_DIR) and overrides cwd to that path
# on container start.
ENTRYPOINT ["sleep", "infinity"]

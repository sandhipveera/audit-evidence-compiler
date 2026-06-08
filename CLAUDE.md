# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Audit Evidence Auto-Compiler: a trust engine for AI-generated compliance evidence. Four independently-trained AI models from competing vendors (Claude, GPT, Gemini, Foundation-Sec-8B) debate compliance evidence pulled from **real Splunk data**, then emit SHA-256 Merkle-chained, tamper-evident audit trails mapped across SOC 2 / ISO 27001 / NIST CSF / NIST 800-53 / COBIT. Single Python package (`src/aec/`), not a monorepo.

## Install / run / test

```bash
pip install -e .                    # core agent (no web)
pip install -e ".[web]"             # + FastAPI dashboard
pip install -e ".[panel-api,web]"   # full: live Splunk + all vendors + web

pytest                              # unit tests
pytest -m integration               # integration tests — need live Splunk/MCP (env-gated: SPLUNK_LIVE_TEST=1)
ruff check src/ cli/                # lint (config below); ruff check --fix to autofix

aec_demo --sample soc2-cc61         # offline demo, no live Splunk
aec_demo --control CC6.1            # live Splunk via MCP
uvicorn web.main:app --port 8000    # web dashboard
```

Ruff config (in `pyproject.toml`): `line-length = 100`, `target-version = "py311"`. Use these, not Black/88 defaults. No separate formatter.

## VM provisioning & stack lifecycle (bash scripts)

- `bash scripts/setup.sh [OPTIONS]` — idempotent one-shot Ubuntu provision (venv, deps, Docker Splunk, BOTS v3 data, AEC web via systemd, Cloudflare Tunnel). Safe to re-run.
- `bash scripts/manage.sh <status|start|stop|restart|logs|update|verify|install-app|install-bots|shell>` — stack lifecycle. `install-app` installs the `auditcompiler` Splunk app + seeds the Compliance Posture dashboard (`scripts/install_splunk_app.sh` + `scripts/seed_posture.py`). `install-bots` restores the real BOTS v3 dataset (`scripts/install_botsv3.sh`) — the 320MB dataset is cached on the host (`~/.aec-data`) so a container rebuild restores 1.9M events in ~30s with no re-download; idempotent (skips if already loaded, `--force` to reinstall). setup.sh runs it automatically.
- Both read `~/.aec-config` (outside the repo; template is `.aec-config.example`) for `GH_TOKEN`, `HF_TOKEN`, `CF_TOKEN`, `SPLUNK_PASSWORD`, `PUBLIC_DOMAIN`, etc. Project `.env` (template `.env.example`) holds runtime config. **Both are gitignored — never commit secrets, and the repo intentionally contains none.**

## Architecture gotchas (read before changing agent behavior)

- **Panel needs genuinely different vendors**, not one model playing four roles. If vendors are unavailable it degrades to Claude-only (`AEC_PANEL_SINGLE_VENDOR_FALLBACK=true`). Consensus is **mechanical, not an LLM tiebreaker**: lowest verdict wins, ordered `PASS < PARTIAL < FAIL < INSUFFICIENT` (INSUFFICIENT outranks FAIL on purpose). This keeps results reproducible.
- **Personas are plain markdown** in `src/aec/agent/personas/{auditor,engineer,adversary,security_model}.md`. Edit them to change behavior — no code change or recompile.
- Only the **adversary** persona may emit counter-searches, and only for one round (no infinite loops); they pass the SPL policy gate (`src/aec/splunk/spl_validator.py`) before running.
- **Splunk MCP transport is pluggable at runtime**: `AEC_SPLUNK_MCP_SERVER=official|livehybrid|rest` selects the backend behind the uniform interface in `src/aec/splunk/client.py`.
- **Evidence snapshots are immutable once captured** — Merkle-chained with canonical JSON (sorted keys, no whitespace), pure SHA-256, no signing. Don't mutate `out/*.jsonl` audit trails or recorded snapshots; `aec verify <report> --trail <trail>.jsonl` recomputes the chain.
- LangGraph pipeline (`src/aec/agent/graph.py`, `nodes.py`): mapper → spl-gen → validator → mcp → panel → consensus → formatter → merkle. HITL gates use `interrupt()`, off by default; enable with `--review interactive`.
- Control catalog `src/aec/priors/catalog.json` is hand-curated and generated via `build_from_xlsx.py` — don't hand-edit; regenerate from source.

# Splunk App: Audit Evidence Compiler

## Overview

The `auditcompiler` Splunk app adds a custom search command `| auditcompiler`
to Splunk's search pipeline. It sends your search results through a four-voice
AI panel debate (Auditor, Engineer, Adversary, Security Model) and returns rows
enriched with compliance verdicts.

## Prerequisites

- Splunk Enterprise 9.x or 10.x
- Python 3.9+ (bundled with Splunk 9+)
- API key for at least one LLM provider (Anthropic required; OpenAI and Google
  optional for multi-vendor mode)

## Building the package

From the repo root:

```bash
chmod +x package.sh
./package.sh
```

This produces `dist/auditcompiler-<version>-<date>.spl`.

### Vendoring third-party dependencies

The `package.sh` script copies `src/aec/` into `bin/lib/aec/`. For full
functionality you also need the LLM SDK dependencies vendored:

```bash
pip install -t splunk-app/auditcompiler/bin/lib/ \
    anthropic openai google-generativeai huggingface-hub httpx \
    pydantic pyyaml requests python-dotenv
```

Run `package.sh` after vendoring to bundle everything into the `.spl`.

## Installation

### Docker (local development)

```bash
docker cp dist/auditcompiler-*.spl splunk:/tmp/auditcompiler.spl
docker exec splunk /opt/splunk/bin/splunk install app /tmp/auditcompiler.spl \
    -auth admin:changeme
docker exec splunk /opt/splunk/bin/splunk restart
```

### Splunk Enterprise (direct)

```bash
cp dist/auditcompiler-*.spl /tmp/
/opt/splunk/bin/splunk install app /tmp/auditcompiler-*.spl
/opt/splunk/bin/splunk restart
```

### Splunk Web UI

1. Settings → Apps → Install app from file
2. Upload the `.spl` file
3. Restart Splunk when prompted

## Configuration

### LLM API Keys

Set environment variables before starting Splunk. Add to
`/opt/splunk/etc/splunk-launch.conf` or your systemd unit file:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...        # optional
GOOGLE_API_KEY=...            # optional
HF_TOKEN=hf_...               # optional Foundation-Sec-8B hosted inference
```

Or export them in the shell before launching Splunk:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
/opt/splunk/bin/splunk restart
```

**Important:** The app makes outbound HTTPS calls to LLM provider APIs
(api.anthropic.com, api.openai.com, generativelanguage.googleapis.com) and
HuggingFace Hosted Inference for Foundation-Sec-8B when `HF_TOKEN` is set.
If your Splunk instance is air-gapped, configure the local Ollama fallback or
document the degraded panel in your deployment plan.

### Single-vendor fallback

If only one LLM provider key is available, the app automatically runs
the loaded personas through that single provider (Claude by default).
Set `AEC_PANEL_SINGLE_VENDOR_FALLBACK=true` (default) to enable this.

## Usage

### Enrich mode (default)

Appends verdict/severity/root_cause columns to every input row:

```spl
index=botsv3 sourcetype=o365:management:activity action=Login
| stats count by user, mfa_used
| auditcompiler control=CC6.1
| table user mfa_used count verdict severity root_cause
```

### Summary mode

Emits a single row with the panel consensus:

```spl
index=botsv3 sourcetype=o365:management:activity action=Login
| stats count by user, mfa_used
| auditcompiler control=CC6.1 mode=summary
```

### Arguments

| Argument    | Required | Default | Values                       |
|-------------|----------|---------|------------------------------|
| `control`   | Yes      | —       | Any control ID (CC6.1, etc.) |
| `framework` | No       | SOC2    | SOC2, ISO27001, NIST_CSF     |
| `mode`      | No       | enrich  | enrich, summary              |

## Troubleshooting

### "Import error: No module named aec"

The vendored dependencies are missing from `bin/lib/`. Re-run the vendoring
steps above and rebuild the `.spl`.

### Panel returns INSUFFICIENT for all rows

Check that at least `ANTHROPIC_API_KEY` is set in Splunk's environment.
Look in `$SPLUNK_HOME/var/log/splunk/python.log` for transport errors.

### Command not found

Verify the app is installed: Settings → Apps should show
"Audit Evidence Compiler". Check `commands.conf` is loading:

```bash
/opt/splunk/bin/splunk btool commands list auditcompiler
```

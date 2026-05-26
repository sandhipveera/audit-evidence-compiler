# Audit Evidence Compiler

AI-powered audit evidence compilation inside Splunk's search pipeline.

## What it does

`| auditcompiler` is a custom Splunk search command that pipes your search results
through a four-voice AI panel debate. Claude, GPT, Gemini, and Foundation-Sec-8B
evaluate the evidence against compliance controls and return enriched rows with
verdict, severity, and root cause columns.

## Usage

```spl
index=botsv3 sourcetype=o365:management:activity action=Login
| stats count by user, mfa_used
| auditcompiler control=CC6.1

| inputlookup admin_logins.csv
| auditcompiler control=CC6.1 framework=SOC2 mode=summary
```

## Arguments

| Argument    | Required | Default | Description                                  |
|-------------|----------|---------|----------------------------------------------|
| `control`   | Yes      | —       | Framework control ID (e.g., CC6.1, A.9.2.1)  |
| `framework` | No       | SOC2    | Framework: SOC2, ISO27001, NIST_CSF           |
| `mode`      | No       | enrich  | `enrich` (per-row) or `summary` (single row)  |

## Requirements

This app requires API keys for at least one LLM provider. Set them in Splunk's
environment before restarting:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Optional for multi-vendor mode:
export OPENAI_API_KEY="sk-..."
export GOOGLE_API_KEY="..."
export HF_TOKEN="hf_..."
```

The app makes outbound HTTPS calls to LLM provider APIs and HuggingFace Hosted
Inference when `HF_TOKEN` is configured.

## Supported Frameworks

- SOC 2 (CC-series controls)
- ISO 27001 (A-series controls)
- NIST CSF (PR/DE/RS controls)

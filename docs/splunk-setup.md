# Splunk Setup Guide

This guide covers provisioning a token for the Audit Evidence Compiler's Splunk REST integration.

## Token Provisioning

### Splunk Enterprise

1. Log into Splunk Web as admin
2. Navigate to **Settings → Tokens** (or **Settings → Users and Authentication → Tokens**)
3. Click **New Token**
4. Configure:
   - **Token name:** `aec-agent`
   - **Audience:** `audit-evidence-compiler`
   - **Expiration:** set per your security policy (recommend 90 days)
5. Click **Create**
6. Copy the token value — it will not be shown again

### Splunk Cloud

1. Log into your Splunk Cloud instance
2. Navigate to **Settings → Tokens**
3. Follow the same steps as Enterprise above
4. Note: your `SPLUNK_HOST` will be `https://<stack-name>.splunkcloud.com:8089`

## Required Permissions

The token must be associated with a role that has:

- **`search`** capability — required for executing SPL queries
- **Index access** — read access to indexes referenced in your control mappings (typically `auth`, `security`, `iam`, or `main`)

Minimal role example:
```
[role_aec_reader]
srchIndexesAllowed = auth;security;iam;main
srchMaxTime = 300
capability = search
```

## Environment Variables

```bash
export SPLUNK_HOST="https://your-splunk:8089"
export SPLUNK_TOKEN="your-bearer-token"
```

Or add to your `.env` file:
```
SPLUNK_HOST=https://your-splunk:8089
SPLUNK_TOKEN=your-bearer-token
```

## Test Connectivity

```bash
python -m aec.splunk.client --probe
```

Expected output:
```
OK — connected to your-server-name at https://your-splunk:8089
```

## Example SPL Search

Once connected, the agent will run searches like:

```spl
index=auth EventCode=4625 OR EventCode=4624
| stats count by user, EventCode
| join user [search index=auth mfa_status=* | stats values(mfa_status) as mfa by user]
```

This query gathers authentication events and MFA enrollment status — the evidence needed for SOC 2 CC6.1 (logical access controls).

## SSL Verification

By default, SSL certificate verification is enabled. For local development with self-signed certs:

```python
from aec.splunk.client import SplunkClient
client = SplunkClient(verify_ssl=False)
```

## Future Work

- **Splunk MCP Server** — agent-native tool integration via the [Splunk MCP Server](https://github.com/splunk/mcp-server-for-splunk)
- **Splunk Cloud token rotation** — automatic refresh before expiry
- **Federated search** — cross-instance queries for multi-tenant environments

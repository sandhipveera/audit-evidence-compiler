# Splunk Enterprise + BOTS v3 Setup Runbook

This runbook covers standing up a local Splunk Enterprise instance with the BOTS v3 dataset for live `aec_demo` runs.

## Prerequisites

- Docker installed and running
- At least 40 GB free disk space (`df -h ~` to check)
- Network access for Docker Hub and the BOTS v3 dataset download

## Step 1: Start Splunk Enterprise

```bash
docker run -d --name splunk \
  -p 8000:8000 -p 8088:8088 -p 8089:8089 \
  -e SPLUNK_START_ARGS="--accept-license" \
  -e SPLUNK_PASSWORD="<choose-a-password>" \
  splunk/splunk:latest
```

Wait for Splunk to be ready (~3 minutes):

```bash
until docker exec splunk /opt/splunk/bin/splunk status 2>/dev/null | grep -q running; do
  echo "Waiting for Splunk to start..."
  sleep 5
done
echo "Splunk is running."
```

Verify: open http://localhost:8000 and log in with `admin` / `<your-password>`.

## Step 2: Download BOTS v3 Dataset

The BOTS v3 (Boss of the SOC v3) dataset is a realistic security dataset created by Splunk. Reference: https://github.com/splunk/botsv3

The dataset is ~30 GB. Download options:

```bash
# Option A: Direct download (if a pre-packaged archive is available)
# Check the botsv3 GitHub repo for current download links.

# Option B: Clone the repo and follow its instructions
git clone https://github.com/splunk/botsv3.git
cd botsv3
# Follow the README for dataset preparation
```

## Step 3: Index BOTS v3 Data

### Create the index

```bash
docker exec splunk /opt/splunk/bin/splunk add index botsv3 \
  -auth admin:<your-password>
```

### Ingest with oneshot (bypasses 500 MB/day free license limit)

Mount the data directory and use `oneshot` to load each log file:

```bash
# Mount data into the container
docker cp /path/to/botsv3/data splunk:/tmp/botsv3data

# Oneshot ingest — does NOT count against daily indexing quota
docker exec splunk /opt/splunk/bin/splunk add oneshot /tmp/botsv3data \
  -index botsv3 \
  -auth admin:<your-password>
```

Alternatively, configure a monitored input for each sourcetype if the data is organized by type.

### Verify ingestion

```bash
# Check index size
docker exec splunk /opt/splunk/bin/splunk list index botsv3 \
  -auth admin:<your-password>
```

Or in Splunk Web: **Settings → Indexes** — confirm `botsv3` shows event count > 0.

### Key sourcetypes to verify

After ingestion, run these searches in Splunk Web to confirm key data is present:

```spl
index=botsv3 | stats count by sourcetype | sort -count
```

Expected sourcetypes include:

| Sourcetype | Description |
|------------|-------------|
| `wineventlog` | Windows security events (login, account mgmt) |
| `aws:cloudtrail` | AWS API call logs |
| `o365:management:activity` | Office 365 audit logs (login, MFA) |
| `iis` | IIS web server access logs |
| `stream:dns` | DNS query/response logs |

## Step 4: Create an API Token

1. In Splunk Web: **Settings → Tokens** (or **Settings → Users and Authentication → Tokens**)
2. Click **New Token**
3. Configure:
   - **Token name:** `aec-agent`
   - **Audience:** `audit-evidence-compiler`
   - **Expiration:** set per your security policy (recommend 90 days)
4. Click **Create**
5. Copy the token value — it will not be shown again

### Set environment variables

Add to your `.env` file:

```bash
SPLUNK_HOST=https://localhost:8089
SPLUNK_TOKEN=<your-token>
```

Or export directly:

```bash
export SPLUNK_HOST=https://localhost:8089
export SPLUNK_TOKEN=<your-token>
```

## Step 5: Probe Connectivity

```bash
python -m aec.splunk.client --probe
```

Expected output:

```json
{
  "ok": true,
  "version": "9.x.x",
  "server": "splunk-hostname",
  "indexes": ["botsv3", "main", ...],
  "botsv3_sourcetypes": ["wineventlog", "aws:cloudtrail", "o365:management:activity", "iis", "stream:dns", ...],
  "botsv3_ready": true
}
```

If `botsv3_ready` is `false`, check that all expected sourcetypes are present in the index.

## Step 6: Run Live Demo

```bash
# First live control — SOC 2 CC6.1 (MFA bypass detection)
aec_demo --control CC6.1 --live

# Without --live, auto-detects from SPLUNK_HOST + SPLUNK_TOKEN
aec_demo --control CC6.1
```

The SPL executed for CC6.1:

```spl
index=botsv3 sourcetype=o365:management:activity action=Login
| stats count by user, mfa_used, src_ip
| where mfa_used="false"
```

## SSL Verification

The Splunk Docker container uses a self-signed certificate. The client disables SSL verification by default in `--probe` and `--live` modes. For production deployments, configure a proper certificate and set `verify_ssl=True`.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Connection refused` on port 8089 | Splunk not started yet — wait for `docker exec splunk /opt/splunk/bin/splunk status` to show `running` |
| `401 Unauthorized` | Token expired or incorrect — regenerate in Settings → Tokens |
| `botsv3` index empty | Oneshot ingest didn't complete — check `docker logs splunk` for errors |
| Disk full during ingest | BOTS v3 needs ~30 GB. Run `df -h` and free space before retrying |
| `500 MB daily indexing limit` | Use `oneshot` upload (Step 3) — it bypasses the daily quota for historic data |

## Required Permissions

The token must be associated with a role that has:

- **`search`** capability
- **Index access** — read access to `botsv3` (and any other indexes you want to query)

Minimal role:

```
[role_aec_reader]
srchIndexesAllowed = botsv3;main
srchMaxTime = 300
capability = search
```

## MCP Server Integration

AEC now supports dual MCP server integration as the primary Splunk transport. See [docs/mcp-setup.md](mcp-setup.md) for:
- Setting up both `splunk-official` and `livehybrid` MCP servers
- Runtime switching via `--mcp` CLI flag or `AEC_SPLUNK_MCP_SERVER` env var
- Automatic fallback between servers

The REST API transport documented above remains available via `--mcp rest`.

## Future Work

- **Splunk Cloud token rotation** — automatic refresh before expiry
- **Federated search** — cross-instance queries for multi-tenant environments

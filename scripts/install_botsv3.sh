#!/usr/bin/env bash
# Install / restore the Splunk BOTS v3 dataset into the aec-splunk container, durably.
#
# The 320MB dataset is cached on the host ($AEC_DATA_DIR, default ~/.aec-data) so a
# container rebuild restores it in ~30s with no re-download. Idempotent: if botsv3
# already has events it's a no-op (use --force to reinstall).
#
#   bash scripts/install_botsv3.sh            # restore if missing
#   bash scripts/install_botsv3.sh --force    # reinstall even if present
#
# Why a script and not just a volume: the dataset ships as a pre-indexed Splunk app
# that lives in /opt/splunk/etc/apps (not on the persistent var volume), so it must be
# re-placed whenever the container is recreated. setup.sh calls this automatically.
set -euo pipefail

CONTAINER="aec-splunk"
MGMT="https://localhost:8089"
DATA_DIR="${AEC_DATA_DIR:-$HOME/.aec-data}"
TGZ="$DATA_DIR/botsv3_data_set.tgz"
APP_SRC="$DATA_DIR/botsv3_data_set"
URL="https://botsdataset.s3.amazonaws.com/botsv3/botsv3_data_set.tgz"
EXPECT_MD5="d7ccca99a01cff070dff3c139cdc10eb"
APP_DST="/opt/splunk/etc/apps/botsv3_data_set"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok(){ echo -e "${GREEN}✓${NC} $*"; }; info(){ echo -e "${CYAN}→${NC} $*"; }
warn(){ echo -e "${YELLOW}⚠${NC}  $*"; }; die(){ echo -e "${RED}✗ FATAL:${NC} $*"; exit 1; }

set -a; [ -f "$HOME/.aec-config" ] && . "$HOME/.aec-config"; [ -f "$(dirname "${BASH_SOURCE[0]}")/../.env" ] && . "$(dirname "${BASH_SOURCE[0]}")/../.env"; set +a
PW="${SPLUNK_PASSWORD:-}"
[ -n "$PW" ] || die "SPLUNK_PASSWORD not set (~/.aec-config / .env)"
docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$" || die "container ${CONTAINER} not running"
api(){ curl -sk -u "admin:${PW}" "$@"; }
bots_count(){ api "${MGMT}/services/search/jobs/export" --data-urlencode "search=| tstats count where index=botsv3" -d output_mode=csv 2>/dev/null | tail -1 | tr -dc '0-9'; }

# 0. idempotent — skip if already loaded
CUR=$(bots_count || echo 0); CUR=${CUR:-0}
if [ "$CUR" -gt 1000 ] && [ "$FORCE" -eq 0 ]; then
  ok "botsv3 already has ${CUR} events — nothing to do (use --force to reinstall)"; exit 0
fi

# 1. ensure host cache (download + verify + extract only if missing)
mkdir -p "$DATA_DIR"
if [ ! -d "$APP_SRC/var" ]; then
  if [ ! -f "$TGZ" ]; then info "downloading BOTS v3 (320MB) → $TGZ"; curl -fSL -o "$TGZ" "$URL"; fi
  GOT=$(md5sum "$TGZ" | cut -d' ' -f1)
  [ "$GOT" = "$EXPECT_MD5" ] || die "tgz md5 mismatch ($GOT != $EXPECT_MD5) — delete $TGZ and retry"
  info "extracting dataset to host cache"; tar xzf "$TGZ" -C "$DATA_DIR"; chmod -R a+rX "$APP_SRC"
fi
ok "host cache ready: $APP_SRC ($(du -sh "$APP_SRC" 2>/dev/null | cut -f1))"

# 2. place the app into the container
info "installing app → ${CONTAINER}:${APP_DST}"
docker exec -u root "$CONTAINER" rm -rf "$APP_DST" 2>/dev/null || true
docker cp "$APP_SRC" "${CONTAINER}:${APP_DST}"
docker exec -u root "$CONTAINER" chown -R splunk:splunk "$APP_DST"

# 3. drop any empty [botsv3] stanza that would shadow the data app's index path
#    (setup.sh's `splunk add index botsv3` writes one into apps/search/local).
CONF="/opt/splunk/etc/apps/search/local/indexes.conf"
if docker exec -u root "$CONTAINER" test -f "$CONF" && docker exec -u root "$CONTAINER" grep -q '^\[botsv3\]' "$CONF"; then
  docker cp "${CONTAINER}:${CONF}" /tmp/_aec_indexes.conf
  python3 - "$@" <<'PY'
import re, pathlib
p = pathlib.Path("/tmp/_aec_indexes.conf"); txt = p.read_text()
# stanza-aware removal of [botsv3] only
out, skip = [], False
for line in txt.splitlines(keepends=True):
    if re.match(r'^\[botsv3\]\s*$', line): skip = True; continue
    if skip and re.match(r'^\[', line): skip = False
    if not skip: out.append(line)
p.write_text("".join(out))
PY
  docker cp /tmp/_aec_indexes.conf "${CONTAINER}:${CONF}"
  docker exec -u root "$CONTAINER" chown splunk:splunk "$CONF"; rm -f /tmp/_aec_indexes.conf
  ok "removed conflicting empty [botsv3] stanza"
fi

# 4. restart (no -auth: Splunk 10.x rejects it) and verify
info "restarting splunkd (~1-2 min)…"
docker exec -u splunk "$CONTAINER" /opt/splunk/bin/splunk restart >/dev/null 2>&1 || true
for i in $(seq 1 60); do
  [ "$(api -o /dev/null -w '%{http_code}' "${MGMT}/services/server/info")" = "200" ] && break; sleep 5
done
sleep 4
N=$(bots_count || echo 0)
[ "${N:-0}" -gt 1000 ] && ok "botsv3 loaded: ${N} events" || die "botsv3 still empty after install — check docker logs ${CONTAINER}"

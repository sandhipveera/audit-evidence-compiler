#!/usr/bin/env bash
set -euo pipefail

APP_DIR="splunk-app/auditcompiler"
VERSION=$(grep 'version' "$APP_DIR/default/app.conf" | head -1 | cut -d= -f2 | tr -d ' ')
OUT="dist/auditcompiler-${VERSION}-$(date +%Y%m%d).spl"

echo "=== Audit Evidence Compiler — Splunk App Packager ==="
echo ""

# Vendor aec into bin/lib/ (copy, not symlink — Splunk needs real files)
echo "Vendoring aec package into bin/lib/ ..."
rm -rf "$APP_DIR/bin/lib/aec"
cp -r src/aec "$APP_DIR/bin/lib/aec"

# Strip __pycache__, .pyc, tests from vendored code
echo "Cleaning bytecode and cache files ..."
find "$APP_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$APP_DIR" -name "*.pyc" -delete 2>/dev/null || true

# Check vendored size
VENDORED_SIZE=$(du -sm "$APP_DIR/bin/lib" 2>/dev/null | cut -f1)
echo "Vendored lib size: ${VENDORED_SIZE:-0} MB"
if [ "${VENDORED_SIZE:-0}" -gt 100 ]; then
    echo "ERROR: Vendored size exceeds 100MB Splunkbase limit" >&2
    exit 1
fi

# Build the .spl tarball
mkdir -p dist
tar -czf "$OUT" -C splunk-app auditcompiler
echo ""
echo "Built: $OUT"
echo ""
echo "Install on Splunk:"
echo "  docker cp $OUT splunk:/tmp/"
echo "  docker exec splunk /opt/splunk/bin/splunk install app /tmp/$(basename "$OUT")"
echo "  docker exec splunk /opt/splunk/bin/splunk restart"

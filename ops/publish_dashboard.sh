#!/usr/bin/env bash
# publish_dashboard.sh — Pull latest dashboard data from VPS, generate HTML, push to gh-pages.
#
# Usage:
#   bash ops/publish_dashboard.sh           # one-shot
#   watch -n 300 bash ops/publish_dashboard.sh  # every 5 min
#
# Requires: SSH access to VPS, git configured with push access to siusunsun/NQ-on-IB

set -e
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VPS="root@178.105.116.241"
REMOTE_DIR="/root/nq_sleeves"
BRANCH="gh-pages"

cd "$REPO_DIR"

# 1. Pull latest data files from VPS
echo "Pulling data from VPS..."
mkdir -p /tmp/nq_dashboard_data
scp -o ConnectTimeout=10 "$VPS:$REMOTE_DIR/nq_sleeves_heartbeat.json" /tmp/nq_dashboard_data/ 2>/dev/null || true
scp -o ConnectTimeout=10 "$VPS:$REMOTE_DIR/nq_sleeves_state.json" /tmp/nq_dashboard_data/ 2>/dev/null || true
scp -o ConnectTimeout=10 "$VPS:$REMOTE_DIR/nq_sleeves_ledger.jsonl" /tmp/nq_dashboard_data/ 2>/dev/null || true
scp -o ConnectTimeout=10 "$VPS:$REMOTE_DIR/nq_sleeves_ledger_ftmo.jsonl" /tmp/nq_dashboard_data/ 2>/dev/null || true

# 2. Generate HTML using the dashboard script with data from VPS
echo "Generating dashboard..."
scp -o ConnectTimeout=10 "$VPS:$REMOTE_DIR/nq_dashboard.html" /tmp/nq_dashboard_data/index.html

# 3. Push to gh-pages
echo "Publishing to gh-pages..."
TMPDIR=$(mktemp -d)
cd "$TMPDIR"
git init -b "$BRANCH"
cp /tmp/nq_dashboard_data/index.html .

# Add a .nojekyll to skip Jekyll processing
touch .nojekyll

git add -A
git commit -m "Dashboard update $(date -u +%Y-%m-%dT%H:%M:%SZ)" --allow-empty 2>/dev/null || true
git remote add origin "https://github.com/siusunsun/NQ-on-IB.git"
git push -f origin "$BRANCH"

cd /
rm -rf "$TMPDIR"
echo "Published to https://siusunsun.github.io/NQ-on-IB/"

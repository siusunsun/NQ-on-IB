#!/bin/bash
# Fast V9 watchdog (2026-08-03) — runs every 1 min via cron.
# Restarts V9 within <=1 min of any death, vs the old <=15 min hsi-watchdog tick that let
# the 2026-08-03 Sunday-open VWAP_LONG_R3 signal (28,589.5) slip through the 07:15-07:30 HKT
# daily-restart crash gap. APPENDS stdout (>>) so crash tracebacks survive for root-causing
# (the hsi watchdog used > which truncated them on every relaunch).
[ -f /root/v9/PAUSED ] && exit 0
ss -ltn 2>/dev/null | grep -q ':7497' || exit 0        # Gateway down: nothing to do
if ! pgrep -f "python v9_run_user.py" >/dev/null 2>&1; then
  echo "$(date -u) V9 down -> relaunch (fast watchdog)" >> /root/v9/v9_watchdog.log
  cd /root/v9 && nohup /root/hsi/venv/bin/python v9_run_user.py >> /root/v9/v9_stdout.log 2>&1 &
fi

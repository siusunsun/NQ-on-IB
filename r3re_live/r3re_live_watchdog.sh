#!/bin/bash
# r3re_live_watchdog.sh — supervisor for the R3 LIVE executor.
# NOT installed in cron by default (the executor is DISARMED and awaiting a human arm).
# Relaunches within <=1 min unless PAUSED. Respects the same PAUSED sentinel the bot idles on.
[ -f /root/r3re_live/PAUSED ] && exit 0
ss -ltn 2>/dev/null | grep -q ':7497' || exit 0
if ! pgrep -f "python r3re_live_bot.py" >/dev/null 2>&1; then
  echo "$(date -u) R3-live down -> relaunch" >> /root/r3re_live/r3re_live_watchdog.log
  cd /root/r3re_live && setsid nohup /root/hsi/venv/bin/python r3re_live_bot.py >> /root/r3re_live/r3re_live_stdout.log 2>&1 < /dev/null &
fi

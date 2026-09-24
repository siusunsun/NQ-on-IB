#!/bin/bash
# launch_nq_sleeves.sh — Start the NQ sleeves bot on VPS via TWS tunnel.
#
# Usage:
#   ./launch_nq_sleeves.sh          # start (TWS tunnel, default)
#   ./launch_nq_sleeves.sh stop     # stop
#   ./launch_nq_sleeves.sh status   # check status
#
# Requires: /root/hsi/venv/bin/python (has numpy, pandas, ib_async)

set -euo pipefail

BOT_DIR="/root/nq_sleeves"
VENV="/root/hsi/venv/bin/python"
LOG="$BOT_DIR/nq_sleeves.log"
PID_FILE="$BOT_DIR/nq_sleeves.pid"
PAUSE_FILE="$BOT_DIR/NQ_SLEEVES_DISABLED.flag"

export NQ_TARGET="${NQ_TARGET:-TWS}"

case "${1:-start}" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PID_FILE"))"
      exit 1
    fi
    echo "Starting NQ Sleeves bot (target=$NQ_TARGET)..."
    cd "$BOT_DIR"
    nohup "$VENV" -u nq_sleeves_live.py >> "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
    echo "Started (PID $!, log: $LOG)"
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      PID=$(cat "$PID_FILE")
      if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Stopped (PID $PID)"
      else
        echo "PID $PID not running"
      fi
      rm -f "$PID_FILE"
    else
      echo "No PID file"
    fi
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Running (PID $(cat "$PID_FILE"))"
      tail -5 "$LOG" 2>/dev/null || true
    else
      echo "Not running"
    fi
    if [ -f "$PAUSE_FILE" ]; then
      echo "PAUSED (flag file exists)"
    fi
    # Heartbeat
    if [ -f "$BOT_DIR/nq_sleeves_heartbeat.json" ]; then
      echo "Last heartbeat:"
      cat "$BOT_DIR/nq_sleeves_heartbeat.json" | python3 -m json.tool 2>/dev/null || cat "$BOT_DIR/nq_sleeves_heartbeat.json"
    fi
    ;;
  pause)
    touch "$PAUSE_FILE"
    echo "Paused (flag created)"
    ;;
  resume)
    rm -f "$PAUSE_FILE"
    echo "Resumed (flag removed)"
    ;;
  *)
    echo "Usage: $0 {start|stop|status|pause|resume}"
    exit 1
    ;;
esac

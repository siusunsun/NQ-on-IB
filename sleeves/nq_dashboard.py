"""nq_dashboard.py — Generate an HTML dashboard comparing BOT / IB / FTMO fills.

Reads:
  - nq_sleeves_ledger.jsonl       (BOT signals + IB fills)
  - nq_sleeves_ledger_ftmo.jsonl  (FTMO fills, from the FTMO-target bot)
  - nq_sleeves_heartbeat.json     (live status)
  - nq_sleeves_state.json         (current positions)
  - ftmo_fills.jsonl              (FTMO bridge fill log, synced from MT5 machine)

Outputs:
  - nq_dashboard.html (static, auto-refreshes every 60s)

Usage:
  python nq_dashboard.py                 # one-shot generate
  python nq_dashboard.py --watch         # regenerate every 60s
  python nq_dashboard.py --serve 8095    # serve on HTTP port
"""
from __future__ import annotations
import argparse
import json
import http.server
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
HERE = Path(__file__).resolve().parent

# ---- data files (all relative to HERE) ----
IB_LEDGER     = HERE / "nq_sleeves_ledger.jsonl"
FTMO_LEDGER   = HERE / "nq_sleeves_ledger_ftmo.jsonl"
FTMO_FILLS    = HERE / "ftmo_fills.jsonl"
HEARTBEAT     = HERE / "nq_sleeves_heartbeat.json"
STATE_FILE    = HERE / "nq_sleeves_state.json"
OUTPUT_HTML   = HERE / "nq_dashboard.html"

KILL_LINES  = {"CCI": -2213, "SNAP": -2969, "TBS": -1646}
REVIEW_LINES = {"CCI": -1660, "SNAP": -2227, "TBS": -1235}
SLEEVES = ["CCI", "SNAP", "TBS"]


def load_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    try:
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                rows.append(json.loads(line))
    except Exception:
        pass
    return rows

def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default or {}

def fmt_ts(ts_str):
    if not ts_str:
        return ""
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts_str)[:16]

def fmt_px(px):
    try:
        return f"{float(px):,.2f}"
    except Exception:
        return str(px)

def fmt_pnl(pnl):
    try:
        v = float(pnl)
        cls = "pos" if v >= 0 else "neg"
        return f'<span class="{cls}">${v:+,.0f}</span>'
    except Exception:
        return str(pnl)


def build_trade_table(ib_rows, ftmo_rows, ftmo_fill_rows):
    """Build a unified trade table: one row per BOT signal, matched to IB and FTMO fills."""
    trades = []

    # Index FTMO fills by approximate time for matching
    ftmo_map = {}
    for r in ftmo_fill_rows:
        key = (r.get("sleeve", ""), r.get("ev", ""), r.get("side", ""))
        ftmo_map.setdefault(key, []).append(r)

    for r in ib_rows:
        trade = {
            "ts": r.get("ts_utc", ""),
            "sleeve": r.get("sleeve", ""),
            "ev": r.get("ev", ""),
            "side": r.get("side", ""),
            # BOT signal
            "bot_px": r.get("px") or r.get("entry", ""),
            "bot_stop": r.get("stop", ""),
            "bot_target": r.get("target", ""),
            # IB fill (same as bot for now — they come from the same ledger)
            "ib_px": r.get("px") or r.get("exit", ""),
            "ib_qty": r.get("qty", ""),
            # P&L
            "pnl_pts": r.get("pnl_pts", ""),
            "pnl_usd": r.get("pnl_usd", ""),
            "cum_pnl": r.get("cum_pnl", ""),
            "reason": r.get("reason", ""),
            # FTMO (try to match)
            "ftmo_px": "",
            "ftmo_slip": "",
        }
        trades.append(trade)

    # FTMO-only rows (from the FTMO ledger)
    for r in ftmo_rows:
        trade = {
            "ts": r.get("ts_utc", ""),
            "sleeve": r.get("sleeve", ""),
            "ev": r.get("ev", ""),
            "side": r.get("side", ""),
            "bot_px": r.get("px") or r.get("entry", ""),
            "bot_stop": r.get("stop", ""),
            "bot_target": r.get("target", ""),
            "ib_px": "",
            "ib_qty": "",
            "pnl_pts": r.get("pnl_pts", ""),
            "pnl_usd": r.get("pnl_usd", ""),
            "cum_pnl": r.get("cum_pnl", ""),
            "reason": r.get("reason", ""),
            "ftmo_px": r.get("px") or r.get("exit", ""),
            "ftmo_slip": "",
        }
        trades.append(trade)

    # Sort by timestamp
    trades.sort(key=lambda t: t.get("ts", ""), reverse=True)
    return trades


def build_summary(ib_rows, state):
    """Per-sleeve summary: trades today, cum P&L, kill line proximity."""
    summary = {}
    for name in SLEEVES:
        sl_state = state.get("sleeves", {}).get(name, {})
        cum_pnl = sl_state.get("cum_pnl", 0)
        kill = KILL_LINES.get(name, -9999)
        review = REVIEW_LINES.get(name, -9999)

        # Count trades from ledger
        entries = [r for r in ib_rows if r.get("sleeve") == name and r.get("ev") == "ENTRY"]
        exits = [r for r in ib_rows if r.get("sleeve") == name and r.get("ev") == "EXIT"]

        summary[name] = {
            "in_position": sl_state.get("in_position", False),
            "side": sl_state.get("side"),
            "entry_price": sl_state.get("entry_price", 0),
            "cum_pnl": cum_pnl,
            "trades_today": sl_state.get("trades_today", 0),
            "total_trades": len(entries),
            "killed": sl_state.get("killed", False),
            "kill_line": kill,
            "review_line": review,
            "kill_pct": min(100, max(0, (cum_pnl / kill * 100))) if kill < 0 else 0,
            "last_trade_ts": exits[-1].get("ts_utc", "") if exits else "",
        }
    return summary


def generate_html():
    ib_rows = load_jsonl(IB_LEDGER)
    ftmo_rows = load_jsonl(FTMO_LEDGER)
    ftmo_fill_rows = load_jsonl(FTMO_FILLS)
    hb = load_json(HEARTBEAT)
    state = load_json(STATE_FILE)

    trades = build_trade_table(ib_rows, ftmo_rows, ftmo_fill_rows)
    summary = build_summary(ib_rows, state)

    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    hb_ts = fmt_ts(hb.get("ts_utc", ""))
    hb_phase = hb.get("phase", "unknown")
    hb_target = hb.get("target", "?")

    # Status color
    if hb_phase == "running":
        status_cls = "status-ok"
    elif hb_phase == "disabled":
        status_cls = "status-warn"
    else:
        status_cls = "status-err"

    # Build sleeve cards
    cards_html = ""
    for name in SLEEVES:
        s = summary[name]
        pos_badge = ""
        if s["in_position"]:
            pos_badge = f'<span class="badge badge-live">{s["side"].upper()} @ {fmt_px(s["entry_price"])}</span>'
        elif s["killed"]:
            pos_badge = '<span class="badge badge-killed">KILLED</span>'
        else:
            pos_badge = '<span class="badge badge-flat">FLAT</span>'

        kill_bar_cls = "bar-ok"
        if s["kill_pct"] > 75:
            kill_bar_cls = "bar-danger"
        elif s["kill_pct"] > 50:
            kill_bar_cls = "bar-warn"

        cards_html += f"""
        <div class="card">
          <div class="card-header">
            <h3>{name}</h3>
            {pos_badge}
          </div>
          <div class="card-body">
            <div class="stat">
              <span class="label">Cum P&L</span>
              <span class="value">{fmt_pnl(s['cum_pnl'])}</span>
            </div>
            <div class="stat">
              <span class="label">Total Trades</span>
              <span class="value">{s['total_trades']}</span>
            </div>
            <div class="stat">
              <span class="label">Today</span>
              <span class="value">{s['trades_today']}</span>
            </div>
            <div class="kill-section">
              <span class="label">Kill Line (${s['kill_line']:,})</span>
              <div class="kill-bar">
                <div class="kill-fill {kill_bar_cls}" style="width:{s['kill_pct']:.0f}%"></div>
              </div>
            </div>
          </div>
        </div>"""

    # Build trade rows
    rows_html = ""
    for t in trades[:100]:  # last 100 trades
        ev_cls = "ev-entry" if t["ev"] == "ENTRY" else "ev-exit"
        rows_html += f"""
        <tr>
          <td>{fmt_ts(t['ts'])}</td>
          <td><span class="sleeve-tag">{t['sleeve']}</span></td>
          <td class="{ev_cls}">{t['ev']}</td>
          <td>{t.get('side', '')}</td>
          <td class="px">{fmt_px(t['bot_px'])}</td>
          <td class="px">{fmt_px(t['ib_px']) if t['ib_px'] else '—'}</td>
          <td class="px">{fmt_px(t['ftmo_px']) if t['ftmo_px'] else '—'}</td>
          <td>{fmt_px(t['bot_stop']) if t['bot_stop'] else '—'}</td>
          <td>{t.get('reason', '')}</td>
          <td>{fmt_pnl(t['pnl_usd']) if t['pnl_usd'] else ''}</td>
          <td>{fmt_pnl(t['cum_pnl']) if t['cum_pnl'] else ''}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>NQ Sleeves Dashboard</title>
<style>
:root {{
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff;
  --pos: #3fb950; --neg: #f85149; --warn: #d29922;
  --entry: #1f6feb33; --exit: #da3633aa;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, 'Segoe UI', Helvetica, sans-serif;
       background: var(--bg); color: var(--text); padding: 16px; }}
h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
.header {{ display: flex; justify-content: space-between; align-items: center;
           padding-bottom: 12px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }}
.header-right {{ text-align: right; font-size: 0.85rem; color: var(--text2); }}
.status {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-weight: 600; font-size: 0.8rem; }}
.status-ok {{ background: #238636; color: #fff; }}
.status-warn {{ background: #9e6a03; color: #fff; }}
.status-err {{ background: #da3633; color: #fff; }}

.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          gap: 12px; margin-bottom: 20px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
.card-header {{ display: flex; justify-content: space-between; align-items: center;
                padding: 10px 14px; border-bottom: 1px solid var(--border); }}
.card-header h3 {{ font-size: 1.1rem; }}
.card-body {{ padding: 12px 14px; }}
.stat {{ display: flex; justify-content: space-between; padding: 4px 0; }}
.label {{ color: var(--text2); font-size: 0.85rem; }}
.value {{ font-weight: 600; }}

.badge {{ padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }}
.badge-live {{ background: #238636; color: #fff; }}
.badge-flat {{ background: var(--border); color: var(--text2); }}
.badge-killed {{ background: #da3633; color: #fff; }}

.kill-section {{ margin-top: 8px; }}
.kill-bar {{ background: var(--border); border-radius: 4px; height: 8px; margin-top: 4px; overflow: hidden; }}
.kill-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
.bar-ok {{ background: var(--pos); }}
.bar-warn {{ background: var(--warn); }}
.bar-danger {{ background: var(--neg); }}

table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
thead {{ position: sticky; top: 0; }}
th {{ background: var(--surface); color: var(--text2); text-align: left; padding: 8px 6px;
      border-bottom: 2px solid var(--border); font-weight: 600; white-space: nowrap; }}
td {{ padding: 6px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
tr:hover {{ background: #1c2128; }}
.px {{ font-family: 'SF Mono', Consolas, monospace; }}
.ev-entry {{ color: var(--accent); font-weight: 600; }}
.ev-exit {{ color: var(--warn); }}
.sleeve-tag {{ background: var(--border); padding: 1px 6px; border-radius: 4px; font-size: 0.8rem; }}
.pos {{ color: var(--pos); font-weight: 600; }}
.neg {{ color: var(--neg); font-weight: 600; }}

.table-wrap {{ overflow-x: auto; }}
.footer {{ margin-top: 16px; font-size: 0.75rem; color: var(--text2); text-align: center; }}

@media (max-width: 600px) {{
  .cards {{ grid-template-columns: 1fr; }}
  body {{ padding: 8px; }}
}}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>NQ Sleeves — Live Dashboard</h1>
    <span style="color:var(--text2); font-size:0.85rem;">CCI AM + SNAP + TBS | 1 MNQ per sleeve</span>
  </div>
  <div class="header-right">
    <span class="status {status_cls}">{hb_phase.upper()}</span><br>
    <span>Target: {hb_target} | {now_et}</span><br>
    <span>Heartbeat: {hb_ts}</span>
  </div>
</div>

<div class="cards">
{cards_html}
</div>

<h2 style="font-size:1.1rem; margin-bottom:10px;">Trade Log</h2>
<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Time (ET)</th><th>Sleeve</th><th>Event</th><th>Side</th>
  <th>BOT Px</th><th>IB Fill</th><th>FTMO Fill</th>
  <th>Stop</th><th>Reason</th><th>P&L</th><th>Cum P&L</th>
</tr>
</thead>
<tbody>
{rows_html if rows_html else '<tr><td colspan="11" style="text-align:center; color:var(--text2); padding:20px;">No trades yet — bot is running, waiting for signals</td></tr>'}
</tbody>
</table>
</div>

<div class="footer">
  Auto-refreshes every 60s &middot; Data from nq_sleeves_ledger.jsonl &middot;
  Kill lines: CCI ${KILL_LINES['CCI']:,} / SNAP ${KILL_LINES['SNAP']:,} / TBS ${KILL_LINES['TBS']:,}
</div>
</body>
</html>"""
    return html


def write_dashboard():
    html = generate_html()
    tmp = Path(str(OUTPUT_HTML) + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, OUTPUT_HTML)


def serve(port):
    """Simple HTTP server for the dashboard."""
    import functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE))
    with http.server.HTTPServer(("0.0.0.0", port), handler) as httpd:
        print(f"Dashboard serving at http://0.0.0.0:{port}/nq_dashboard.html")
        httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="NQ Sleeves Dashboard")
    parser.add_argument("--watch", action="store_true", help="Regenerate every 60s")
    parser.add_argument("--serve", type=int, metavar="PORT", help="Serve on HTTP port")
    args = parser.parse_args()

    write_dashboard()
    print(f"Dashboard written to {OUTPUT_HTML}")

    if args.serve:
        # Start regeneration thread
        def regen_loop():
            while True:
                time.sleep(60)
                try:
                    write_dashboard()
                except Exception as e:
                    print(f"regen failed: {e!r}")
        t = threading.Thread(target=regen_loop, daemon=True)
        t.start()
        serve(args.serve)
    elif args.watch:
        while True:
            time.sleep(60)
            try:
                write_dashboard()
                print(f"Regenerated at {datetime.now(ET):%H:%M:%S ET}")
            except Exception as e:
                print(f"regen failed: {e!r}")


if __name__ == "__main__":
    main()

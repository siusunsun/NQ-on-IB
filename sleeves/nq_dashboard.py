"""nq_dashboard.py — NQ v9.2 Dashboard: Theoretical / IB / FTMO three-layer comparison.

Reads:
  /root/v9/v9_live_heartbeat.json   (bot status, sleeve states)
  /root/v9/v9_live_trade_log.json   (IB fills from record_v9_fill)
  /root/v9/v9_ftmo_fills.jsonl      (FTMO fills from EA, synced from MT5)

Outputs:
  nq_dashboard.html (static, auto-refreshes every 60s)
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
HERE = Path(__file__).resolve().parent

# ---- data files ----
V9_DIR        = Path("/root/v9")
HEARTBEAT     = V9_DIR / "v9_live_heartbeat.json"
IB_TRADE_LOG  = V9_DIR / "v9_live_trade_log.json"
FTMO_FILLS    = V9_DIR / "v9_ftmo_fills.jsonl"
OUTPUT_HTML   = HERE / "nq_dashboard.html"

SLEEVES = ["VWAP_LONG_R3", "R3_REV", "CCI_AM", "TLB_BEAR_SHORT"]
SLEEVE_SHORT = {"VWAP_LONG_R3": "VWAP_R3", "R3_REV": "R3_REV",
                "CCI_AM": "CCI_AM", "TLB_BEAR_SHORT": "TBS"}
SLEEVE_COLORS = {"VWAP_LONG_R3": "#58a6ff", "R3_REV": "#bc8cff",
                 "CCI_AM": "#3fb950", "TLB_BEAR_SHORT": "#f0883e"}

V2_START = "2026-09-25"


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default or {}


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


def fmt_ts_utc(ts_str):
    if not ts_str:
        return ""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%m-%d %H:%M")
    except Exception:
        return str(ts_str)[:16]


def fmt_px(px):
    try:
        return f"{float(px):,.2f}"
    except Exception:
        return "—"


def fmt_pnl(pnl):
    try:
        v = float(pnl)
        cls = "pos" if v >= 0 else "neg"
        return f'<span class="{cls}">${v:+,.0f}</span>'
    except Exception:
        return ""


def build_ib_trades(fills):
    """Pair entry/exit fills into round-trip trades for the table."""
    trades = []
    open_entries = {}
    for f in fills:
        sleeve = f.get("instrument", "")
        if sleeve not in SLEEVES:
            continue
        if f.get("time", "") < V2_START:
            continue
        label = f.get("label", "")
        if "ENTRY" in label:
            open_entries[sleeve] = f
            trades.append({
                "time": f["time"],
                "sleeve": sleeve,
                "event": "ENTRY",
                "side": f.get("side", ""),
                "signal_px": f.get("signal_px"),
                "ib_px": f.get("price"),
                "stop": f.get("stop"),
                "target": f.get("target"),
                "pnl_usd": None,
                "pnl_R": None,
                "reason": None,
            })
        else:
            trades.append({
                "time": f["time"],
                "sleeve": sleeve,
                "event": "EXIT",
                "side": f.get("side", ""),
                "signal_px": f.get("signal_px"),
                "ib_px": f.get("price"),
                "stop": None,
                "target": None,
                "pnl_usd": f.get("realized"),
                "pnl_R": f.get("realized_R"),
                "reason": f.get("reason", label),
            })
            open_entries.pop(sleeve, None)
    trades.sort(key=lambda t: t.get("time", ""), reverse=True)
    return trades


def build_ftmo_index(ftmo_rows):
    """Index FTMO fills by (sleeve, event, approx time) for matching."""
    idx = {}
    for r in ftmo_rows:
        key = (r.get("sleeve", ""), r.get("event", ""))
        idx.setdefault(key, []).append(r)
    return idx


def match_ftmo(trade, ftmo_idx):
    """Find the FTMO fill that matches an IB trade."""
    key = (trade["sleeve"], trade["event"])
    candidates = ftmo_idx.get(key, [])
    if not candidates:
        return None
    trade_time = trade.get("time", "")
    best = None
    best_dt = timedelta(hours=999)
    for c in candidates:
        try:
            ct = datetime.strptime(c.get("time", "")[:19], "%Y-%m-%d %H:%M:%S")
            tt = datetime.strptime(trade_time[:19], "%Y-%m-%d %H:%M:%S")
            dt = abs(ct - tt)
            if dt < best_dt and dt < timedelta(minutes=5):
                best_dt = dt
                best = c
        except Exception:
            continue
    return best


def generate_html():
    hb = load_json(HEARTBEAT)
    ib_log = load_json(IB_TRADE_LOG, {"fills": []})
    ftmo_rows = load_jsonl(FTMO_FILLS)
    ftmo_idx = build_ftmo_index(ftmo_rows)

    fills = ib_log.get("fills", [])
    trades = build_ib_trades(fills)

    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    hb_ts = fmt_ts(hb.get("last_heartbeat", ""))
    connected = hb.get("connected", False)
    halted = hb.get("halted", False)
    regime = hb.get("regime_cell", "?")
    cum_pnl = hb.get("cum_realized_usd", 0)

    if halted:
        status_cls, status_txt = "status-err", "HALTED"
    elif connected:
        status_cls, status_txt = "status-ok", "RUNNING"
    else:
        status_cls, status_txt = "status-warn", "DISCONNECTED"

    # Sleeve cards
    cards_html = ""
    sleeves_data = hb.get("sleeves", {})
    for name in SLEEVES:
        s = sleeves_data.get(name, {})
        color = SLEEVE_COLORS.get(name, "#58a6ff")
        short = SLEEVE_SHORT.get(name, name)
        active = s.get("active_today", False)
        in_pos = s.get("in_position", False)
        side = s.get("side", "")
        entry_px = s.get("entry_price")
        r_today = s.get("realized_R", 0)
        trades_today = s.get("trade_count_today", 0)

        if in_pos and side:
            badge = f'<span class="badge badge-live">{side.upper()} @ {fmt_px(entry_px)}</span>'
        elif not active:
            badge = '<span class="badge badge-inactive">INACTIVE</span>'
        else:
            badge = '<span class="badge badge-flat">FLAT</span>'

        # Cum P&L from IB trade log (v2 fills only)
        sleeve_pnl = sum(f.get("realized", 0) or 0 for f in fills
                         if f.get("instrument") == name
                         and f.get("time", "") >= V2_START
                         and f.get("realized") is not None)
        sleeve_trades = sum(1 for f in fills
                            if f.get("instrument") == name
                            and f.get("time", "") >= V2_START
                            and "ENTRY" in f.get("label", ""))

        cards_html += f"""
        <div class="card" style="border-top: 3px solid {color}">
          <div class="card-header">
            <h3>{short}</h3>
            {badge}
          </div>
          <div class="card-body">
            <div class="stat">
              <span class="label">IB Cum P&L</span>
              <span class="value">{fmt_pnl(sleeve_pnl) if sleeve_trades > 0 else '<span class="muted">—</span>'}</span>
            </div>
            <div class="stat">
              <span class="label">Trades (v9.2)</span>
              <span class="value">{sleeve_trades}</span>
            </div>
            <div class="stat">
              <span class="label">Today</span>
              <span class="value">{trades_today}</span>
            </div>
            <div class="stat">
              <span class="label">R Today</span>
              <span class="value">{r_today:+.2f}R</span>
            </div>
          </div>
        </div>"""

    # Trade log rows
    rows_html = ""
    for t in trades[:80]:
        ev_cls = "ev-entry" if t["event"] == "ENTRY" else "ev-exit"
        color = SLEEVE_COLORS.get(t["sleeve"], "#58a6ff")
        short = SLEEVE_SHORT.get(t["sleeve"], t["sleeve"])

        # FTMO matching
        ftmo = match_ftmo(t, ftmo_idx)
        ftmo_px = fmt_px(ftmo["fill_px"]) if ftmo else "—"

        # Slippage
        slip_html = ""
        if t["signal_px"] and t["ib_px"]:
            slip = abs(t["ib_px"] - t["signal_px"])
            slip_html = f'{slip:.1f}'

        rows_html += f"""
        <tr>
          <td>{fmt_ts_utc(t['time'])}</td>
          <td><span class="sleeve-tag" style="border-left:3px solid {color}">{short}</span></td>
          <td class="{ev_cls}">{t['event']}</td>
          <td>{t.get('side', '')}</td>
          <td class="px">{fmt_px(t.get('signal_px'))}</td>
          <td class="px">{fmt_px(t.get('ib_px'))}</td>
          <td class="px">{ftmo_px}</td>
          <td class="px slip">{slip_html}</td>
          <td>{t.get('reason') or '—'}</td>
          <td>{fmt_pnl(t['pnl_usd']) if t['pnl_usd'] is not None else ''}</td>
          <td>{f"{t['pnl_R']:+.2f}R" if t['pnl_R'] is not None else ''}</td>
        </tr>"""

    # FTMO cumulative
    ftmo_cum = sum(r.get("pnl_usd", 0) for r in ftmo_rows if r.get("event") == "EXIT")
    ftmo_count = sum(1 for r in ftmo_rows if r.get("event") == "ENTRY")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>NQ v9.2 Dashboard</title>
<style>
:root {{
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff;
  --pos: #3fb950; --neg: #f85149; --warn: #d29922;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, 'Segoe UI', Helvetica, sans-serif;
       background: var(--bg); color: var(--text); padding: 16px; }}
h1 {{ font-size: 1.4rem; margin-bottom: 2px; }}
.subtitle {{ color: var(--text2); font-size: 0.85rem; }}
.header {{ display: flex; justify-content: space-between; align-items: flex-start;
           padding-bottom: 12px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }}
.header-right {{ text-align: right; font-size: 0.82rem; color: var(--text2); }}
.status {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-weight: 600; font-size: 0.8rem; }}
.status-ok {{ background: #238636; color: #fff; }}
.status-warn {{ background: #9e6a03; color: #fff; }}
.status-err {{ background: #da3633; color: #fff; }}

.summary-bar {{ display: flex; gap: 24px; margin-bottom: 16px; flex-wrap: wrap; }}
.summary-item {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
                 padding: 10px 16px; min-width: 140px; }}
.summary-item .label {{ font-size: 0.75rem; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }}
.summary-item .value {{ font-size: 1.2rem; font-weight: 700; margin-top: 2px; }}

.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
          gap: 12px; margin-bottom: 20px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
.card-header {{ display: flex; justify-content: space-between; align-items: center;
                padding: 10px 14px; border-bottom: 1px solid var(--border); }}
.card-header h3 {{ font-size: 1.05rem; }}
.card-body {{ padding: 10px 14px; }}
.stat {{ display: flex; justify-content: space-between; padding: 3px 0; }}
.label {{ color: var(--text2); font-size: 0.83rem; }}
.value {{ font-weight: 600; }}
.muted {{ color: var(--text2); }}

.badge {{ padding: 2px 8px; border-radius: 10px; font-size: 0.73rem; font-weight: 600; }}
.badge-live {{ background: #238636; color: #fff; }}
.badge-flat {{ background: var(--border); color: var(--text2); }}
.badge-inactive {{ background: #21262d; color: #484f58; }}

table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
thead {{ position: sticky; top: 0; z-index: 1; }}
th {{ background: var(--surface); color: var(--text2); text-align: left; padding: 8px 5px;
      border-bottom: 2px solid var(--border); font-weight: 600; white-space: nowrap; }}
td {{ padding: 5px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
tr:hover {{ background: #1c2128; }}
.px {{ font-family: 'SF Mono', Consolas, monospace; font-size: 0.8rem; }}
.slip {{ color: var(--warn); font-size: 0.78rem; }}
.ev-entry {{ color: var(--accent); font-weight: 600; }}
.ev-exit {{ color: var(--warn); }}
.sleeve-tag {{ padding: 1px 6px; border-radius: 4px; font-size: 0.78rem; background: var(--border); }}
.pos {{ color: var(--pos); font-weight: 600; }}
.neg {{ color: var(--neg); font-weight: 600; }}
.table-wrap {{ overflow-x: auto; }}
.section-title {{ font-size: 1.05rem; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }}
.section-title .count {{ font-size: 0.8rem; color: var(--text2); font-weight: 400; }}
.footer {{ margin-top: 16px; font-size: 0.72rem; color: var(--text2); text-align: center; }}
@media (max-width: 600px) {{
  .cards {{ grid-template-columns: 1fr; }}
  .summary-bar {{ gap: 8px; }}
  body {{ padding: 8px; }}
}}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>NQ v9.2 — Live Dashboard</h1>
    <span class="subtitle">VWAP_R3 + R3_REV + CCI_AM + TBS &middot; 1 MNQ (IB) + $100 risk (FTMO)</span>
  </div>
  <div class="header-right">
    <span class="status {status_cls}">{status_txt}</span><br>
    <span>Regime: <b>{regime}</b> &middot; {now_et}</span><br>
    <span>Heartbeat: {hb_ts}</span>
  </div>
</div>

<div class="summary-bar">
  <div class="summary-item">
    <div class="label">IB P&L (v9.2)</div>
    <div class="value">{fmt_pnl(cum_pnl)}</div>
  </div>
  <div class="summary-item">
    <div class="label">FTMO P&L</div>
    <div class="value">{fmt_pnl(ftmo_cum) if ftmo_count > 0 else '<span class="muted">awaiting sync</span>'}</div>
  </div>
  <div class="summary-item">
    <div class="label">IB Trades</div>
    <div class="value">{sum(1 for f in fills if f.get("time","") >= V2_START and "ENTRY" in f.get("label",""))}</div>
  </div>
  <div class="summary-item">
    <div class="label">FTMO Trades</div>
    <div class="value">{ftmo_count if ftmo_count > 0 else '<span class="muted">—</span>'}</div>
  </div>
</div>

<div class="cards">
{cards_html}
</div>

<div class="section-title">
  Trade Log <span class="count">({len(trades)} fills)</span>
</div>
<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Time (ET)</th><th>Sleeve</th><th>Event</th><th>Side</th>
  <th>Signal</th><th>IB Fill</th><th>FTMO Fill</th><th>Slip</th>
  <th>Reason</th><th>P&L</th><th>R</th>
</tr>
</thead>
<tbody>
{rows_html if rows_html else '<tr><td colspan="11" style="text-align:center; color:var(--text2); padding:24px;">No v9.2 trades yet — bot armed, waiting for signals</td></tr>'}
</tbody>
</table>
</div>

<div class="footer">
  Auto-refreshes every 60s &middot; IB fills from v9_live_trade_log.json &middot; FTMO fills from v9_ftmo_fills.jsonl &middot;
  Daily loss limit: $2,000
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
    import functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE))
    with http.server.HTTPServer(("0.0.0.0", port), handler) as httpd:
        print(f"Dashboard serving at http://0.0.0.0:{port}/nq_dashboard.html")
        httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="NQ v9.2 Dashboard")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--serve", type=int, metavar="PORT")
    args = parser.parse_args()

    write_dashboard()
    print(f"Dashboard written to {OUTPUT_HTML}")

    if args.serve:
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

"""_ra_vwap_sweep.py - VWAP_LONG_R3 / VWAP_SHORT_R1 parameter sensitivity on FULL history,
split by era (2021-2023 vs 2024-2026). Also emits ungated variants for the regime-gate test.
READ-ONLY. Writes only _ra_* files.
"""
import sys, json
sys.path.insert(0, "/root/v9/bt")
import numpy as np, pandas as pd
import importlib.util
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)

bt = C.bt; vsl = C.vsl
ET = C.ET

print("[load]", flush=True)
E = C.load_env()
df = E["df"]; bars5 = E["bars5"]; regime = E["regime"]; idx = E["idx"]
print("  1min=%d 5min=%d regime_days=%d" % (len(df), len(bars5), len(regime)), flush=True)

WIN = [(0, 360), (570, 720), (720, 870), (1140, 1440)]
is_in_window = bt.is_in_window
no_trade = bt.no_trade

# ---- config grids (one-at-a-time perturbation around deployed) ----
BASE_L = dict(k=2.0, nbo=5, sl=20, r=3.0)
BASE_S = dict(k=2.0, nbo=3, sl=20, r=1.0)
GRID_L = dict(k=[1.5, 2.0, 2.5], nbo=[4, 5, 6], sl=[15, 20, 25], r=[2.0, 3.0, 4.0])
GRID_S = dict(k=[1.5, 2.0, 2.5], nbo=[2, 3, 4], sl=[15, 20, 25], r=[0.5, 1.0, 1.5])

def variants(base, grid):
    out = []
    for p, vals in grid.items():
        for v in vals:
            c = dict(base); c[p] = v
            out.append((p, v, c))
    return out

VL = variants(BASE_L, GRID_L)
VS = variants(BASE_S, GRID_S)

# unique (k,nbo,sl) signal states
def sig_keys(vs):
    return sorted({(c["k"], c["nbo"], c["sl"]) for _, _, c in vs})

KL = sig_keys(VL); KS = sig_keys(VS)
print("  signal-states: LONG=%d SHORT=%d" % (len(KL), len(KS)), flush=True)

states = {}
for (k, nbo, sl) in KL:
    states[("LONG", k, nbo, sl)] = vsl.VwapState(side="LONG", entry_band_k=k, n_bars_outside=nbo,
                                                 swing_lookback=sl, post_halt_blackout_bars=24)
for (k, nbo, sl) in KS:
    states[("SHORT", k, nbo, sl)] = vsl.VwapState(side="SHORT", entry_band_k=k, n_bars_outside=nbo,
                                                  swing_lookback=sl)

# 5-min bucket ts -> position in 1-min index of the bar at bucket_ts+5min
pos_of = {}
sigs = {kk: [] for kk in states}
print("[pass over 5min bars]", flush=True)
for j, (bts, bo, bh, bl, bc, bv) in enumerate(bars5):
    et_t = bts.tz_convert(ET).time(); et_d = bts.tz_convert(ET).date()
    reg = regime.get(et_d)
    inwin = is_in_window(et_t, WIN)
    hol = no_trade(et_d)
    entry_utc = bts + pd.Timedelta(minutes=5)
    py = bts.to_pydatetime()
    for kk, st in states.items():
        s = st.on_bar(py, bh, bl, bc, bv, anchor_hour_utc=0)
        if s is None:
            continue
        sigs[kk].append((entry_utc, s["entry_price"], s["stop_price"], et_d, inwin, hol,
                         (reg.cell if reg else "NONE")))
    if j % 100000 == 0:
        print("   bar %d/%d" % (j, len(bars5)), flush=True)

# resolve 1-min positions
all_ts = sorted({t for v in sigs.values() for (t, *_r) in v})
posmap = {}
for t in all_ts:
    p = idx.searchsorted(t, side="left")
    posmap[t] = int(p)

LONG_CELLS = ("BOTH_BULL", "ZONE_A")
SHORT_CELLS = ("BOTH_BEAR",)

def replay(side, k, nbo, sl, r, gated=True):
    key = (side, k, nbo, sl)
    trs = []
    busy = -1
    direction = "long" if side == "LONG" else "short"
    cells = LONG_CELLS if side == "LONG" else SHORT_CELLS
    for (t, epx, spx, et_d, inwin, hol, cell) in sigs[key]:
        if hol or not inwin:
            continue
        if gated and cell not in cells:
            continue
        p = posmap[t]
        if p >= len(idx) or p <= busy:
            continue
        risk = (epx - spx) if side == "LONG" else (spx - epx)
        if risk <= 0 or risk > C.VWAP_MAX_STOP_PTS:
            continue
        tgt = epx + r * risk if side == "LONG" else epx - r * risk
        out = C.sim_vwap_fast(E, p, direction, epx, spx, tgt)
        busy = out["exit_pos"]
        trs.append((et_d, out["pnl_usd"], out["exit_reason"], risk))
    return trs

def era(et_d):
    return "2021-23" if et_d.year <= 2023 else "2024-26"

def summarize(trs, tag):
    if not trs:
        return dict(tag=tag, n=0)
    pnl = np.array([x[1] for x in trs])
    yrs = np.array([x[0].year for x in trs])
    full = C.stat_block(pnl)
    e1 = C.stat_block(pnl[yrs <= 2023]); e2 = C.stat_block(pnl[yrs >= 2024])
    return dict(tag=tag, n=full["n"], net=round(full["net"]), pf=round(full["pf"], 3),
                wr=round(full["wr"], 3), sr=round(full["sr"], 4),
                n1=e1["n"], net1=round(e1["net"]), pf1=round(e1["pf"], 3),
                n2=e2["n"], net2=round(e2["net"]), pf2=round(e2["pf"], 3))

print("\n[replay]", flush=True)
rows = []
for side, vs in (("LONG", VL), ("SHORT", VS)):
    seen = set()
    for (p, v, c) in vs:
        sig = (c["k"], c["nbo"], c["sl"], c["r"])
        if sig in seen:
            # still record which param this is a variant of
            pass
        seen.add(sig)
        trs = replay(side, c["k"], c["nbo"], c["sl"], c["r"], gated=True)
        d = summarize(trs, "%s|%s=%s" % (side, p, v))
        d.update(side=side, param=p, value=v, k=c["k"], nbo=c["nbo"], sl=c["sl"], r=c["r"],
                 base=int(sig == (BASE_L["k"], BASE_L["nbo"], BASE_L["sl"], BASE_L["r"]) if side == "LONG"
                          else sig == (BASE_S["k"], BASE_S["nbo"], BASE_S["sl"], BASE_S["r"])))
        rows.append(d)
        print("  %-22s n=%-4d net=%-8s pf=%-6s | e1 n=%-4d net=%-8s pf=%-6s | e2 n=%-4d net=%-8s pf=%s"
              % (d["tag"], d["n"], d["net"], d["pf"], d["n1"], d["net1"], d["pf1"],
                 d["n2"], d["net2"], d["pf2"]), flush=True)

# ---- ungated baselines (for regime-gate value test) ----
print("\n[ungated baselines]", flush=True)
ung = {}
for side, b in (("LONG", BASE_L), ("SHORT", BASE_S)):
    trs = replay(side, b["k"], b["nbo"], b["sl"], b["r"], gated=False)
    d = summarize(trs, "%s|UNGATED" % side)
    ung[side] = d
    print("  %-22s n=%-4d net=%-8s pf=%-6s | e1 n=%-4d net=%-8s pf=%-6s | e2 n=%-4d net=%-8s pf=%s"
          % (d["tag"], d["n"], d["net"], d["pf"], d["n1"], d["net1"], d["pf1"],
             d["n2"], d["net2"], d["pf2"]), flush=True)
    # dump ungated trades for cross-sleeve analysis
    pd.DataFrame(trs, columns=["et_date", "pnl_usd", "exit_reason", "risk"]).to_csv(
        "/root/v9/bt/_ra_ungated_%s.csv" % side, index=False)

pd.DataFrame(rows).to_csv("/root/v9/bt/_ra_vwap_sweep.csv", index=False)
print("\nwrote /root/v9/bt/_ra_vwap_sweep.csv", flush=True)
print("DONE", flush=True)

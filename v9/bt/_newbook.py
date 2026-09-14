# -*- coding: utf-8 -*-
"""NEW LIVE BOOK monthly/yearly figures (post 2026-08-27 audit changes):
   VWAP_LONG_R3 x2 + VWAP_SHORT_R1 x1 (cut from 2) + TLB_HYB40 x1 + R3_RE(max_wait=48) x1
All NET of $2.04 per round-turn per contract. Compares vs the OLD book (short x2, R3 uncapped).
Run ALONE — this box is memory-tight (see vps-memory-limit-oom)."""
import sys, json, importlib.util
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd

RT = 2.04   # round-turn cost per contract
BT = '/root/v9/bt/'

# --- R3_RE with max_wait=48 (and uncapped, for the delta) via the audit's sweep engine ---
spec = importlib.util.spec_from_file_location("_sw", BT + "_ra_r3re_sweep.py")
# don't exec the whole module (it runs the sweep); instead re-implement by importing its deps
_s = importlib.util.spec_from_file_location("_ra_common", BT + "_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
src = open(BT + "_ra_r3re_sweep.py", encoding="utf-8").read()
head = src.split("rows = []")[0]          # everything up to the sweep driver
ns = {"__name__": "_sw_partial"}
exec(compile(head, "_sw_head", "exec"), ns)
run = ns["run"]
print("[R3] engine loaded", flush=True)

def r3_daily(max_wait):
    fires = run(wait=2, stop_src="si", magnet="adaptive", max_wait=max_wait)
    if not fires: return pd.Series(dtype=float), 0
    d = pd.DataFrame(fires, columns=["yr", "usd", "dt"])
    d["usd"] = d["usd"] - RT                      # 1 contract
    return d.groupby("dt")["usd"].sum(), len(d)

R48, n48 = r3_daily(48)
RUN, nun = r3_daily(None)
print(f"[R3] max_wait=48 n={n48} net=${R48.sum():,.0f}   uncapped n={nun} net=${RUN.sum():,.0f}", flush=True)

# --- other three sleeves from the full-history trade files ---
def load(tag, qty):
    d = pd.read_csv(BT + f'_hist_tr_{tag}.csv')
    col = [c for c in d.columns if 'entry' in c.lower() and ('date' in c.lower() or 'utc' in c.lower() or 'time' in c.lower())][0]
    t = pd.to_datetime(d[col], utc=True, errors='coerce')
    if t.isna().all(): t = pd.to_datetime(d[col], errors='coerce').dt.tz_localize('UTC')
    d['dt'] = t.dt.tz_convert('America/New_York').dt.date
    pc = [c for c in d.columns if c.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')][0]
    d['net'] = d[pc] * qty - RT * qty
    return d.groupby('dt')['net'].sum()

VL = load('VWAP_LONG_R3', 2)
VS1 = load('VWAP_SHORT_R1', 1)      # NEW size
VS2 = load('VWAP_SHORT_R1', 2)      # OLD size
TL = load('TLB_HYB40', 1)

start = min(x.index.min() for x in [VL, VS1, TL, R48])
end   = max(x.index.max() for x in [VL, VS1, TL, R48])
I = pd.Index(sorted(set(pd.bdate_range(start, end).date) | set(VL.index) | set(VS1.index) | set(TL.index) | set(R48.index) | set(RUN.index)))
I = pd.Index([d for d in I if start <= d <= end])
al = lambda s: s.reindex(I).fillna(0.0)
NEW = al(VL) + al(VS1) + al(TL) + al(R48)
OLD = al(VL) + al(VS2) + al(TL) + al(RUN)
ANN = 252.0

def stats(x):
    x = np.asarray(x, float); cu = np.cumsum(x); dd = cu - np.maximum.accumulate(cu)
    sd = x.std(ddof=1); nz = x[x != 0]; w = nz[nz > 0]; l = nz[nz < 0]; neg = x[x < 0]
    ds = neg.std(ddof=1) if len(neg) > 1 else 0
    flat = 0; mx = 0; peak = cu[0]
    for v in cu:
        if v >= peak: peak = v; flat = 0
        else: flat += 1; mx = max(mx, flat)
    return dict(net=float(cu[-1]), ann=float(x.mean()*ANN), sharpe=float(x.mean()/sd*np.sqrt(ANN)) if sd>0 else 0.0,
        sortino=float(x.mean()/ds*np.sqrt(ANN)) if ds>0 else 0.0, maxdd=float(dd.min()),
        retdd=float(cu[-1]/abs(dd.min())) if dd.min()<0 else 0.0,
        pf=float(w.sum()/abs(l.sum())) if l.sum()!=0 else 0.0, ntd=int(len(nz)), flat=int(mx))

out = {'window':[str(I.min()), str(I.max()), len(I)],
       'new': stats(NEW), 'old': stats(OLD),
       'sleeves': {'VWAP_R3_x2': float(al(VL).sum()), 'VWAP_short_x1': float(al(VS1).sum()),
                   'TLB_x1': float(al(TL).sum()), 'R3_48_x1': float(al(R48).sum())}}
yr = pd.Series(pd.to_datetime(I).year, index=I)
out['yearly'] = {int(y): {'new': stats(NEW[(yr==y).values]), 'old': stats(OLD[(yr==y).values]),
                          'ndays': int((yr==y).sum())} for y in sorted(set(yr))}
mo = pd.Series(pd.to_datetime(I).to_period('M').astype(str), index=I)
nm = NEW.groupby(mo).sum(); om = OLD.groupby(mo).sum()
out['monthly'] = [{'m': k, 'new': round(float(nm[k])), 'old': round(float(om[k]))} for k in nm.index]
# within-month drawdown
mdd = []
for m, g in NEW.groupby(mo):
    c = np.cumsum(np.asarray(g, float)); mdd.append(float((c - np.maximum.accumulate(c)).min()))
mdd = np.array(mdd); a = nm.values
out['mo_stats'] = {'mean': float(a.mean()), 'median': float(np.median(a)), 'pos': float(100*(a>0).mean()),
    'worst': float(a.min()), 'best': float(a.max()), 'std': float(a.std(ddof=1)), 'n': int(len(a)),
    'topshare': float(100*a.max()/a.sum()), 'top3': float(100*np.sort(a)[-3:].sum()/a.sum()),
    'dd_mean': float(mdd.mean()), 'dd_median': float(np.median(mdd)), 'dd_worst': float(mdd.min()),
    'dd_p25': float(np.percentile(mdd,25)), 'dd_p10': float(np.percentile(mdd,10))}
print("JSON_START"); print(json.dumps(out)); print("JSON_END")

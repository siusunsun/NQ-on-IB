# -*- coding: utf-8 -*-
"""Does adding SNAPBACK improve the NEW live book? Full history, net of cost.
NEW BOOK = VWAP_R3 x2 + VWAP_SHORT x1 + TLB x1 + R3_RE(max_wait=48) x1
Tests snapback at 1 MNQ (tradable minimum), 0.5, 0.25 (Sharpe-optimal), and the
'scale the book 4x, snap stays 1 lot' scenario. Caches R3_48 daily series.
Run ALONE — box is memory-tight."""
import sys, json, os, importlib.util
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
RT = 2.04; BT = '/root/v9/bt/'
CACHE = BT + '_r3_48_daily.csv'

# ---- R3_RE @ max_wait=48 (cached after first run) ----
if os.path.exists(CACHE):
    _t = pd.read_csv(CACHE); _t['dt'] = pd.to_datetime(_t['date']).dt.date
    R48 = _t.set_index('dt')['usd']; print("[R3] from cache", flush=True)
else:
    _s = importlib.util.spec_from_file_location("_ra_common", BT + "_ra_common.py")
    C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
    head = open(BT + "_ra_r3re_sweep.py", encoding="utf-8").read().split("rows = []")[0]
    ns = {"__name__": "_p"}; exec(compile(head, "_h", "exec"), ns)
    fires = ns["run"](wait=2, stop_src="si", magnet="adaptive", max_wait=48)
    d = pd.DataFrame(fires, columns=["yr", "usd", "dt"]); d["usd"] = d["usd"] - RT
    R48 = d.groupby("dt")["usd"].sum()
    pd.DataFrame({'date': [str(i) for i in R48.index], 'usd': R48.values}).to_csv(CACHE, index=False)
    print("[R3] computed + cached", flush=True)

def load(tag, qty):
    d = pd.read_csv(BT + f'_hist_tr_{tag}.csv')
    col = [c for c in d.columns if 'entry' in c.lower() and ('date' in c.lower() or 'utc' in c.lower() or 'time' in c.lower())][0]
    t = pd.to_datetime(d[col], utc=True, errors='coerce')
    if t.isna().all(): t = pd.to_datetime(d[col], errors='coerce').dt.tz_localize('UTC')
    d['dt'] = t.dt.tz_convert('America/New_York').dt.date
    pc = [c for c in d.columns if c.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')][0]
    d['net'] = d[pc]*qty - RT*qty
    return d.groupby('dt')['net'].sum()

VL = load('VWAP_LONG_R3', 2); VS = load('VWAP_SHORT_R1', 1); TL = load('TLB_HYB40', 1)
sn = pd.read_csv(BT + '_rb_snap_daily.csv'); sn['dt'] = pd.to_datetime(sn['date']).dt.date
SNAP = sn.set_index('dt')['pnl_usd']          # 1 MNQ unit

start = min(x.index.min() for x in [VL, VS, TL, R48]); end = max(x.index.max() for x in [VL, VS, TL, R48])
I = pd.Index(sorted(set(pd.bdate_range(start, end).date) | set(VL.index)|set(VS.index)|set(TL.index)|set(R48.index)|set(SNAP.index)))
I = pd.Index([d for d in I if start <= d <= end])
al = lambda s: s.reindex(I).fillna(0.0)
BOOK = al(VL)+al(VS)+al(TL)+al(R48); SN = al(SNAP)
ANN = 252.0
def stats(x):
    x=np.asarray(x,float); cu=np.cumsum(x); dd=cu-np.maximum.accumulate(cu); sd=x.std(ddof=1)
    nz=x[x!=0]; w=nz[nz>0]; l=nz[nz<0]; neg=x[x<0]; ds=neg.std(ddof=1) if len(neg)>1 else 0
    return dict(net=float(cu[-1]), ann=float(x.mean()*ANN), sharpe=float(x.mean()/sd*np.sqrt(ANN)) if sd>0 else 0,
        sortino=float(x.mean()/ds*np.sqrt(ANN)) if ds>0 else 0, maxdd=float(dd.min()),
        retdd=float(cu[-1]/abs(dd.min())) if dd.min()<0 else 0,
        pf=float(w.sum()/abs(l.sum())) if l.sum()!=0 else 0, dstd=float(sd))
print("\nsnapback standalone daily sd $%.0f   book daily sd $%.0f"%(SN.std(ddof=1), BOOK.std(ddof=1)))
print("snapback standalone: net $%.0f  Sharpe %.2f  maxDD $%.0f"%(SN.sum(), stats(SN)['sharpe'], stats(SN)['maxdd']))

out={'window':[str(I.min()),str(I.max()),len(I)]}
PORT={'BOOK (no snap)':BOOK}
for w in [0.25,0.5,1.0]:
    PORT[f'+snap {w}x']=BOOK+SN*w
print("\n=== ADDING SNAPBACK TO THE NEW BOOK (full history, net) ===")
print(f"{'portfolio':<18}{'net$':>9}{'ann$':>9}{'Sharpe':>8}{'Sortino':>9}{'maxDD$':>10}{'ret/DD':>8}{'PF':>6}")
base=stats(BOOK)
for k,v in PORT.items():
    s=stats(v)
    print(f"{k:<18}{s['net']:>9,.0f}{s['ann']:>9,.0f}{s['sharpe']:>8.2f}{s['sortino']:>9.2f}{s['maxdd']:>10,.0f}{s['retdd']:>8.2f}{s['pf']:>6.2f}")
    out[k]=s
# scaled-book scenario: book x4, snap 1 lot (=0.25 relative)
print("\n=== IF THE BOOK WERE SCALED (snap stays at the 1-lot minimum) ===")
print(f"{'scenario':<28}{'net$':>10}{'Sharpe':>8}{'maxDD$':>10}{'ret/DD':>8}")
for k in [1,2,3,4]:
    b=BOOK*k; s0=stats(b); s1=stats(b+SN*1.0)
    print(f"{'book '+str(k)+'x, no snap':<28}{s0['net']:>10,.0f}{s0['sharpe']:>8.2f}{s0['maxdd']:>10,.0f}{s0['retdd']:>8.2f}")
    print(f"{'book '+str(k)+'x + snap 1 lot':<28}{s1['net']:>10,.0f}{s1['sharpe']:>8.2f}{s1['maxdd']:>10,.0f}{s1['retdd']:>8.2f}   dSharpe {s1['sharpe']-s0['sharpe']:+.2f}")
    out[f'book{k}x']={'no':s0,'snap':s1}
# monthly for book+snap1 vs book
mo=pd.Series(pd.to_datetime(I).to_period('M').astype(str),index=I)
bm=BOOK.groupby(mo).sum(); sm=(BOOK+SN).groupby(mo).sum()
def ms(a):
    a=a.values; return dict(mean=float(a.mean()),median=float(np.median(a)),pos=float(100*(a>0).mean()),
        worst=float(a.min()),best=float(a.max()),std=float(a.std(ddof=1)))
print("\n=== MONTHLY ===")
for k,v in [('book',bm),('book+snap 1 lot',sm)]:
    d=ms(v); print(f"  {k:<18} mean ${d['mean']:>6.0f}  median ${d['median']:>6.0f}  pos {d['pos']:.0f}%  worst ${d['worst']:>7.0f}  best ${d['best']:>6.0f}  std ${d['std']:.0f}")
# per-year dSharpe at 1 lot
yr=pd.Series(pd.to_datetime(I).year,index=I)
print("\n=== PER-YEAR: does snap@1lot help or hurt? (net $ delta / Sharpe delta) ===")
for y in sorted(set(yr)):
    m=(yr==y).values; s0=stats(BOOK[m]); s1=stats((BOOK+SN)[m])
    print(f"  {y}: dNet ${s1['net']-s0['net']:>+7,.0f}   dSharpe {s1['sharpe']-s0['sharpe']:>+5.2f}   dMaxDD ${s1['maxdd']-s0['maxdd']:>+7,.0f}")

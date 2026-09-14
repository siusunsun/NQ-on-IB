# -*- coding: utf-8 -*-
"""Recompute the NQ book headline + monthly table at REALISTIC cost.
Measured 2026-08-31 reconciliation: 1.88 MNQ pts slippage per round-turn ($3.76)
+ $1.22 commission = ~$5.40 per contract round-turn, vs the $2.04 previously modelled.
Read-only; prints old vs new side by side."""
import sys, json, math
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT = '/root/v9/bt/'
CAP = 22500.0

def book(RT):
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
    r = pd.read_csv(BT + '_r3_48_daily.csv'); r['dt'] = pd.to_datetime(r['date']).dt.date
    # cached R3 series was built at 2.04; re-derive gross then re-charge
    R3 = r.set_index('dt')['usd']
    n_r3 = pd.read_csv(BT + '_r3v_fires.csv').shape[0]
    R3 = R3 + (2.04 - RT) * 0  # daily aggregate: adjust below per-trade instead
    r3t = pd.read_csv(BT + '_r3v_fires.csv')
    r3t['dt'] = pd.to_datetime(r3t['entry_date_et']).dt.date
    R3 = (r3t['pnl_usd_1mnq'] - RT).groupby(r3t['dt']).sum()
    start = min(x.index.min() for x in [VL,VS,TL,R3]); end = max(x.index.max() for x in [VL,VS,TL,R3])
    I = pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(R3.index)))
    I = pd.Index([d for d in I if start <= d <= end])
    al = lambda s: s.reindex(I).fillna(0.0)
    B = al(VL)+al(VS)+al(TL)+al(R3)
    return B, I, {'VWAP_R3x2':al(VL).sum(),'VWAP_shortx1':al(VS).sum(),'TLBx1':al(TL).sum(),'R3x1':al(R3).sum()}

def stats(B, I):
    x = np.asarray(B, float); cu = np.cumsum(x); dd = cu-np.maximum.accumulate(cu)
    yrs = (pd.Timestamp(I[-1])-pd.Timestamp(I[0])).days/365.25
    sd = x.std(ddof=1); neg = x[x<0]
    nz = x[x!=0]; w = nz[nz>0]; l = nz[nz<0]
    net = cu[-1]; mdd = abs(dd.min())
    return dict(net=net, ann=net/yrs, yrs=yrs,
        sharpe=x.mean()/sd*math.sqrt(252), sortino=x.mean()/neg.std(ddof=1)*math.sqrt(252),
        pf=w.sum()/abs(l.sum()), maxdd=-mdd, retdd=net/mdd,
        cagr=((CAP+net)/CAP)**(1/yrs)-1, ret_pct=(net/yrs)/CAP, dd_pct=mdd/CAP)

rows = {}
for lbl, RT in (('OLD $2.04', 2.04), ('REALISTIC $5.40', 5.40)):
    B, I, sl = book(RT); s = stats(B, I); rows[lbl] = (s, sl, B, I)

print(f"window {rows['OLD $2.04'][3][0]} .. {rows['OLD $2.04'][3][-1]}   capital ${CAP:,.0f}\n")
print(f"{'metric':<20}{'OLD $2.04':>16}{'REALISTIC $5.40':>18}")
for k, f in [('net','${:,.0f}'),('ann','${:,.0f}'),('cagr','{:.1%}'),('ret_pct','{:.1%}'),
             ('sharpe','{:.2f}'),('sortino','{:.2f}'),('pf','{:.2f}'),('maxdd','${:,.0f}'),
             ('dd_pct','{:.1%}'),('retdd','{:.2f}')]:
    a = rows['OLD $2.04'][0][k]; b = rows['REALISTIC $5.40'][0][k]
    print(f"{k:<20}{f.format(a):>16}{f.format(b):>18}")
print("\nper-sleeve net (realistic):")
for k, v in rows['REALISTIC $5.40'][1].items(): print(f"   {k:<14} ${v:>9,.0f}")

# monthly table at realistic cost
B, I = rows['REALISTIC $5.40'][2], rows['REALISTIC $5.40'][3]
mo = pd.Series(pd.to_datetime(I).to_period('M').astype(str), index=I)
m = B.groupby(mo).sum()
eq = CAP; out = {}
for k, v in m.items():
    y, mm = k.split('-'); out.setdefault(y, {})[int(mm)] = 100*v/eq; eq += v
print("\nMONTHLY % (realistic cost, % of running equity):")
print('YEAR '+''.join(f'{x:>7}' for x in ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'])+f'{"YTD":>8}')
J = {}
for y in sorted(out):
    cells=''; ytd=1.0; arr=[None]*12
    for i in range(1,13):
        if i in out[y]:
            cells+=f'{out[y][i]:>+7.1f}'; ytd*=(1+out[y][i]/100); arr[i-1]=round(out[y][i],1)
        else: cells+=f'{"—":>7}'
    print(f'{y} '+cells+f'{100*(ytd-1):>+7.1f}%')
    J[y]={'months':arr,'ytd':round(100*(ytd-1),1)}
print(f"\nfinal equity ${eq:,.0f}   cumulative {100*(eq/CAP-1):.1f}%")
print("JSONMONTHLY:"+json.dumps(J))

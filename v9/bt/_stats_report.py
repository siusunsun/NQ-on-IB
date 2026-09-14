# -*- coding: utf-8 -*-
"""Full-report stats: BASELINE (VWAP-R3 2MNQ + VWAP-short 2MNQ + TLB-hybrid INTRABAR 1MNQ)
vs BASELINE + R3 re-entry (1MNQ, live-config revalidated). Honest numbers. Read-only.
Emits headline stats, yearly, monthly, drawdowns, and weekly equity curve as JSON for the report."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT = '/root/v9/bt/'; MNQ = 2.0

def etdate(s):
    return pd.to_datetime(s, utc=True, errors='coerce').dt.tz_convert('America/New_York').dt.date

def v9_vwap(fn, qty):
    d = pd.read_csv(BT + fn); d['dt'] = pd.to_datetime(d['entry_time_et']).dt.date
    return d.assign(usd=d['pnl_usd'] * qty).groupby('dt')['usd'].sum()

VL = v9_vwap('trades_VWAP_LONG_R3.csv', 2)
VS = v9_vwap('trades_VWAP_SHORT_R1.csv', 2)
# TLB corrected INTRABAR hybrid (1 MNQ)
tl = pd.read_csv(BT + '_tlbib_hybrid_trades.csv')
tcol = 'entry_date' if 'entry_date' in tl.columns else ('entry_time_et' if 'entry_time_et' in tl.columns else tl.columns[0])
tl['dt'] = pd.to_datetime(tl[tcol]).dt.date
TL = tl.assign(usd=tl['pnl_usd'] * 1).groupby('dt')['usd'].sum()
# R3 re-entry corrected (1 MNQ)
r3 = pd.read_csv(BT + '_r3v_fires.csv')
rcol = 'entry_date_et' if 'entry_date_et' in r3.columns else 'entry_time_et'
r3['dt'] = pd.to_datetime(r3[rcol]).dt.date
R3 = r3.assign(usd=r3['pnl_usd_1mnq'] * 1).groupby('dt')['usd'].sum()

start = TL.index.min(); end = max(TL.index.max(), VL.index.max(), R3.index.max())
allidx = sorted(set(list(VL.index)+list(VS.index)+list(TL.index)+list(R3.index)))
I = pd.Index([d for d in allidx if start <= d <= end], name='dt')
# full calendar between start/end using business days as fallback
full = pd.bdate_range(start, end).date
I = pd.Index(sorted(set(full) | set(I)), name='dt')
al = lambda s: s.reindex(I).fillna(0.0)
VLa, VSa, TLa, R3a = al(VL), al(VS), al(TL), al(R3)
BASE = VLa + VSa + TLa
COMBO = BASE + R3a
ANN = 252.0

def stats(x):
    x = np.asarray(x, float); cu = np.cumsum(x); pk = np.maximum.accumulate(cu); dd = cu-pk
    sd = x.std(ddof=1); sh = x.mean()/sd*np.sqrt(ANN) if sd>0 else 0.0
    neg = x[x<0]; dstd = neg.std(ddof=1) if len(neg)>1 else 0
    sortino = x.mean()/dstd*np.sqrt(ANN) if dstd>0 else 0.0
    nz = x[x!=0]; wins = nz[nz>0]; loss = nz[nz<0]
    pf = wins.sum()/abs(loss.sum()) if loss.sum()!=0 else 0.0
    flat=0; mx=0; peak=cu[0]
    for v in cu:
        if v>=peak: peak=v; flat=0
        else: flat+=1; mx=max(mx,flat)
    return dict(net=float(cu[-1]), ann=float(x.mean()*ANN), sharpe=float(sh), sortino=float(sortino),
        maxdd=float(dd.min()), retdd=float(cu[-1]/abs(dd.min())) if dd.min()<0 else 0.0, pf=float(pf),
        win_day=float(100*(nz>0).mean()) if len(nz) else 0.0, ntd=int(len(nz)),
        posday=float(100*(x>0).mean()), best=float(x.max()), worst=float(x.min()),
        avgwin=float(wins.mean()) if len(wins) else 0.0, avgloss=float(loss.mean()) if len(loss) else 0.0,
        longest_flat=int(mx))

out = {}
out['window'] = [str(I.min()), str(I.max()), len(I)]
out['headline'] = {'baseline': stats(BASE), 'combo': stats(COMBO)}
out['sleeves'] = {k: float(al(s).sum()) for k,s in [('VWAP_R3',VL),('VWAP_short',VS),('TLB_hybrid',TL),('R3_reentry',R3)]}
yr = pd.Series(pd.to_datetime(I).year, index=I)
out['yearly'] = {}
for y in sorted(set(yr)):
    m=(yr==y).values
    out['yearly'][int(y)] = {'base': stats(BASE[m]), 'combo': stats(COMBO[m]),
        'r3': float(R3a[m].sum()), 'ndays': int(m.sum())}
mo = pd.Series(pd.to_datetime(I).to_period('M').astype(str), index=I)
bm = BASE.groupby(mo).sum(); cm = COMBO.groupby(mo).sum()
out['monthly'] = [{'m':k,'base':round(float(bm[k]),0),'combo':round(float(cm[k]),0)} for k in bm.index]
out['monthly_summary'] = {
    'base': {'pos':float(100*(bm>0).mean()),'worst':float(bm.min()),'best':float(bm.max()),'avg':float(bm.mean()),'std':float(bm.std())},
    'combo':{'pos':float(100*(cm>0).mean()),'worst':float(cm.min()),'best':float(cm.max()),'avg':float(cm.mean()),'std':float(cm.std())},
    'combo_beats': float(100*(cm>bm).mean())}
# drawdowns of combo
cu = np.cumsum(np.asarray(COMBO,float)); pk=np.maximum.accumulate(cu); dds=cu-pk; dts=list(I)
eps=[]; i=0
while i<len(dds):
    if dds[i]<0:
        j=i
        while j<len(dds) and dds[j]<0: j+=1
        seg=dds[i:j]; tr=i+int(np.argmin(seg))
        eps.append({'depth':round(float(seg.min()),0),'start':str(dts[i]),'trough':str(dts[tr]),
                    'recover':str(dts[min(j,len(dts)-1)]),'dur':int(j-i)}); i=j
    else: i+=1
out['drawdowns'] = sorted(eps, key=lambda e:e['depth'])[:6]
# weekly equity curve
eqb = pd.Series(np.cumsum(np.asarray(BASE,float)), index=pd.to_datetime(I))
eqc = pd.Series(np.cumsum(np.asarray(COMBO,float)), index=pd.to_datetime(I))
wk = eqb.resample('W').last().dropna(); wc = eqc.resample('W').last().dropna()
out['equity'] = {'dates':[str(d.date()) for d in wk.index],
                 'base':[round(float(v),0) for v in wk.values],
                 'combo':[round(float(v),0) for v in wc.reindex(wk.index).values]}
print("JSON_START"); print(json.dumps(out)); print("JSON_END")

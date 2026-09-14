# -*- coding: utf-8 -*-
"""Combined-portfolio full stats: BASELINE (mod V9) vs BASELINE + CCI + Snapback + R3.
Reuses the exact series-building convention from _combo_master_fixed.py. Read-only."""
import sys, glob, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT = '/root/v9/bt/'
NQ_DIR = '/root/v9/data_1m/NQ/'
MNQ = 2.0

def etdate(s):
    t = pd.to_datetime(s, utc=True, errors='coerce')
    return t.dt.tz_convert('America/New_York').dt.date

def v9_vwap(fn, qty):
    d = pd.read_csv(BT + fn)
    d['dt'] = pd.to_datetime(d['entry_time_et']).dt.date
    return d.assign(usd=d['pnl_usd'] * qty).groupby('dt')['usd'].sum()

VL = v9_vwap('trades_VWAP_LONG_R3.csv', 2)
VS = v9_vwap('trades_VWAP_SHORT_R1.csv', 2)
th = pd.read_csv(BT + '_combo_tlbhyb_trades.csv'); th['dt'] = etdate(th['entry_utc'])
TL = th.assign(usd=th['pnl_usd'] * 1).groupby('dt')['usd'].sum()

cci = pd.read_csv(BT + '_cci_trades.csv'); cci = cci[cci['slot'] == 'AM'].copy()
cci['dt'] = pd.to_datetime(cci['date']).dt.date
CCI = cci.assign(usd=cci['pts'] * MNQ * 1).groupby('dt')['usd'].sum()

def load_1m():
    parts = [pd.read_csv(f, usecols=['time', 'close']) for f in sorted(glob.glob(NQ_DIR + '*.csv'))]
    d = pd.concat(parts, ignore_index=True); d['time'] = pd.to_datetime(d['time'], utc=True, errors='coerce')
    return d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')['close']
c1m = load_1m()
day = c1m.groupby(c1m.index.normalize()).last(); day.index = day.index.tz_localize(None); day = day.dropna()
z = (day - day.rolling(20).mean()) / day.rolling(20).std(ddof=0)
dfz = pd.DataFrame({'c': day, 'z': z}).dropna()
c = dfz['c'].values; zv = dfz['z'].values; dpts = np.r_[0.0, np.diff(c)]
pos = (zv < -1.5).astype(float); expo = np.r_[0.0, pos[:-1]]
dp = np.abs(np.diff(np.r_[0.0, expo])); costpts = (0.00015 / 2) * c * dp
snap_pts = pd.Series(expo * dpts - costpts, index=pd.to_datetime(dfz.index).date)
SNAP = snap_pts * MNQ * 0.25
CAL = pd.Series(0.0, index=pd.to_datetime(day.index).date)

r3 = pd.read_csv(BT + '_r3re_fires.csv'); r3['dt'] = etdate(r3['entry_utc'])
R3 = r3.assign(usd=r3['pts'] * MNQ * 1).groupby('dt')['usd'].sum()

start = TL.index.min(); end = max(SNAP.index.max(), TL.index.max(), VL.index.max())
I = pd.Index(sorted([d for d in CAL.index if start <= d <= end]), name='dt')
al = lambda s: s.reindex(I).fillna(0.0)
VLa, VSa, TLa, CCa, SNa, R3a = [al(s) for s in [VL, VS, TL, CCI, SNAP, R3]]
BASE = VLa + VSa + TLa
COMBO = BASE + CCa + SNa + R3a
ANN = 252.0

def stats(x):
    x = np.asarray(x, float); cu = np.cumsum(x); pk = np.maximum.accumulate(cu); dd = cu - pk
    sd = x.std(ddof=1); sh = x.mean()/sd*np.sqrt(ANN) if sd>0 else float('nan')
    neg = x[x<0]; dstd = neg.std(ddof=1) if len(neg)>1 else 0
    sortino = x.mean()/dstd*np.sqrt(ANN) if dstd>0 else float('nan')
    nz = x[x!=0]; wins = nz[nz>0]; loss = nz[nz<0]
    pf = wins.sum()/abs(loss.sum()) if loss.sum()!=0 else float('inf')
    # longest flat (no new equity high) in days
    flat=0; mx=0; peak=cu[0]
    for v in cu:
        if v>=peak: peak=v; flat=0
        else: flat+=1; mx=max(mx,flat)
    return dict(net=cu[-1], ann=x.mean()*ANN, sharpe=sh, sortino=sortino, maxdd=dd.min(),
                retdd=cu[-1]/abs(dd.min()) if dd.min()<0 else float('nan'),
                pf=pf, win_day=100*(nz>0).mean() if len(nz) else 0, ntrade_days=len(nz),
                posday=100*(x>0).mean(), best=x.max(), worst=x.min(), avgwin=wins.mean() if len(wins) else 0,
                avgloss=loss.mean() if len(loss) else 0, longest_flat=mx)

print("="*78); print("HEADLINE STATS  (window %s .. %s, %d trading days)"%(I.min(), I.max(), len(I))); print("="*78)
sb, sc = stats(BASE), stats(COMBO)
print(f"{'metric':<20}{'BASELINE':>16}{'+CCI+Snap+R3':>16}")
for k,lab,fmt in [('net','Net $','${:,.0f}'),('ann','Annualized $','${:,.0f}'),('sharpe','Sharpe','{:.2f}'),
    ('sortino','Sortino','{:.2f}'),('pf','Profit Factor','{:.2f}'),('win_day','Win% (days)','{:.0f}%'),
    ('maxdd','Max DD $','${:,.0f}'),('retdd','Return/DD','{:.2f}'),('longest_flat','Longest flat (days)','{:.0f}'),
    ('posday','% positive days','{:.0f}%'),('best','Best day $','${:,.0f}'),('worst','Worst day $','${:,.0f}'),
    ('avgwin','Avg win-day $','${:,.0f}'),('avgloss','Avg loss-day $','${:,.0f}')]:
    print(f"{lab:<20}{fmt.format(sb[k]):>16}{fmt.format(sc[k]):>16}")

# YEARLY
print("\n"+"="*78); print("YEARLY  (net $, Sharpe, maxDD $, ret/DD)"); print("="*78)
yr = pd.Series(pd.to_datetime(I).year, index=I)
print(f"{'year':<8}{'BASE net':>11}{'COMBO net':>11}{'B Sh':>7}{'C Sh':>7}{'B maxDD':>10}{'C maxDD':>10}{'C r/DD':>8}")
for y in sorted(set(yr)):
    m = (yr==y).values; b=BASE[m]; cc=COMBO[m]; sbb=stats(b); scc=stats(cc)
    print(f"{y:<8}{sbb['net']:>11,.0f}{scc['net']:>11,.0f}{sbb['sharpe']:>7.2f}{scc['sharpe']:>7.2f}"
          f"{sbb['maxdd']:>10,.0f}{scc['maxdd']:>10,.0f}{scc['retdd']:>8.2f}")
print("  add-on standalone yearly $:")
for nm,s in [('CCI',CCa),('Snap',SNa),('R3',R3a)]:
    print(f"    {nm:<5}"+" ".join(f"{y}:{s[(yr==y).values].sum():>+7,.0f}" for y in sorted(set(yr))))

# MONTHLY full table
print("\n"+"="*78); print("MONTHLY net $  (BASE vs COMBO, delta)"); print("="*78)
mo = pd.Series(pd.to_datetime(I).to_period('M').astype(str), index=I)
bm = BASE.groupby(mo).sum(); cm = COMBO.groupby(mo).sum()
print(f"{'month':<10}{'BASE $':>10}{'COMBO $':>10}{'delta $':>10}")
for m in bm.index:
    print(f"{m:<10}{bm[m]:>10,.0f}{cm[m]:>10,.0f}{cm[m]-bm[m]:>+10,.0f}")
def mstats(mser):
    return dict(pos=100*(mser>0).mean(), best=mser.max(), worst=mser.min(), avg=mser.mean(), std=mser.std(), n=len(mser))
mb, mc = mstats(bm), mstats(cm)
print(f"\n  BASE : %pos {mb['pos']:.0f}  worst ${mb['worst']:,.0f}  best ${mb['best']:,.0f}  avg ${mb['avg']:,.0f}  std ${mb['std']:,.0f}  n={mb['n']}")
print(f"  COMBO: %pos {mc['pos']:.0f}  worst ${mc['worst']:,.0f}  best ${mc['best']:,.0f}  avg ${mc['avg']:,.0f}  std ${mc['std']:,.0f}  n={mc['n']}")
print(f"  COMBO beats BASE in {100*(cm>bm).mean():.0f}% of months")

# top drawdowns of COMBO
print("\n"+"="*78); print("TOP DRAWDOWNS (combined portfolio, daily)"); print("="*78)
cu = np.cumsum(np.asarray(COMBO,float)); pk=np.maximum.accumulate(cu); ddser=cu-pk
dts=list(I); episodes=[]; i=0
while i < len(ddser):
    if ddser[i] < 0:
        j=i
        while j<len(ddser) and ddser[j]<0: j+=1
        seg=ddser[i:j]; tr=i+int(np.argmin(seg))
        episodes.append((float(seg.min()), dts[i], dts[tr], dts[min(j,len(dts)-1)], j-i)); i=j
    else: i+=1
for depth,st,tr,rec,dur in sorted(episodes)[:5]:
    print(f"  ${depth:>8,.0f}  start {st}  trough {tr}  recover {rec}  dur {dur}d")
print("\nDONE")

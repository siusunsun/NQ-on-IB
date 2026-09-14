# -*- coding: utf-8 -*-
"""_combo_master: decision-grade portfolio comparison. Read-only.
Daily $ P&L at LIVE sizes, common date index, metrics + decision analysis."""
import sys, glob, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
BT = '/root/v9/bt/'
NQ_DIR = '/root/v9/data_1m/NQ/'
MNQ = 2.0  # $/pt per 1 MNQ


def etdate(s):
    t = pd.to_datetime(s, utc=True, errors='coerce')
    return t.dt.tz_convert('America/New_York').dt.date


# Baseline V9 (modified): VWAP sleeves from CSV (pnl_usd@1MNQ), TLB hybrid regenerated
def v9_vwap(fn, qty):
    d = pd.read_csv(BT + fn)
    d['dt'] = pd.to_datetime(d['entry_time_et']).dt.date
    return d.assign(usd=d['pnl_usd'] * qty).groupby('dt')['usd'].sum()


VL = v9_vwap('trades_VWAP_LONG_R3.csv', 2)   # live 2 MNQ
VS = v9_vwap('trades_VWAP_SHORT_R1.csv', 2)  # live 2 MNQ
th = pd.read_csv(BT + '_combo_tlbhyb_trades.csv')
th['dt'] = etdate(th['entry_utc'])
TL = th.assign(usd=th['pnl_usd'] * 1).groupby('dt')['usd'].sum()  # live 1 MNQ, hybrid stop

# CCI single-slot AM, 1 MNQ
cci = pd.read_csv(BT + '_cci_trades.csv')
cci = cci[cci['slot'] == 'AM'].copy()
cci['dt'] = pd.to_datetime(cci['date']).dt.date
CCI = cci.assign(usd=cci['pts'] * MNQ * 1).groupby('dt')['usd'].sum()

# Snapback daily points (midnight anchor, 1 contract) -> 0.25 MNQ-equiv
def load_1m():
    parts = [pd.read_csv(f, usecols=['time', 'close']) for f in sorted(glob.glob(NQ_DIR + '*.csv'))]
    d = pd.concat(parts, ignore_index=True)
    d['time'] = pd.to_datetime(d['time'], utc=True, errors='coerce')
    return d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')['close']


c1m = load_1m()
day = c1m.groupby(c1m.index.normalize()).last()
day.index = day.index.tz_localize(None)
day = day.dropna()
z = (day - day.rolling(20).mean()) / day.rolling(20).std(ddof=0)
dfz = pd.DataFrame({'c': day, 'z': z}).dropna()
c = dfz['c'].values
zv = dfz['z'].values
dpts = np.r_[0.0, np.diff(c)]
pos = (zv < -1.5).astype(float)
expo = np.r_[0.0, pos[:-1]]
COST_FRAC = 0.00015
dp = np.abs(np.diff(np.r_[0.0, expo]))
costpts = (COST_FRAC / 2) * c * dp
snap_pts = pd.Series(expo * dpts - costpts, index=pd.to_datetime(dfz.index).date)
SNAP = snap_pts * MNQ * 0.25   # 0.25 MNQ-equiv (vol-sized per study)
CAL = pd.Series(0.0, index=pd.to_datetime(day.index).date)  # full NQ trading-day calendar

# TLB reclaim, 1 MNQ (net_usd already at 1 MNQ)
rc = pd.read_csv(BT + '_tlbrc_fires.csv')
rc['dt'] = etdate(rc['fire_time'])
RCLM = rc.assign(usd=rc['net_usd'] * 1).groupby('dt')['usd'].sum()

# R3 re-entry overlay, 1 MNQ (pts -> usd)
r3 = pd.read_csv(BT + '_r3re_fires.csv')
r3['dt'] = etdate(r3['entry_utc'])
R3 = r3.assign(usd=r3['pts'] * MNQ * 1).groupby('dt')['usd'].sum()

series = {'VL': VL, 'VS': VS, 'TL': TL, 'CCI': CCI, 'SNAP': SNAP, 'RCLM': RCLM, 'R3': R3}
print("=== NATIVE COVERAGE (first..last active date, n active days, net$ native-window) ===")
for k, s in series.items():
    print(f"  {k:5s} {str(s.index.min()):>12} .. {str(s.index.max()):<12} nActiveDays={len(s):>4} net=${s.sum():>10,.0f}")

# Coverage = period each strategy is SIMULATED over the shared NQ archive, NOT last-fire date.
# Baseline hybrid-TLB harness is the latest-starting sim (data start ~2024-08-20); every add-on
# has earlier data. All backtests run to the archive end (~2026-08-25). A sleeve not firing on a
# day is a flat day, not end-of-coverage -> do NOT truncate at a sleeve's last trade.
start = TL.index.min()                                   # baseline harness / data start
end = max(SNAP.index.max(), TL.index.max(), VL.index.max())  # archive end
cal = [d for d in CAL.index if start <= d <= end]
I = pd.Index(sorted(cal), name='dt')


def al(s):
    return s.reindex(I).fillna(0.0)


VLa, VSa, TLa, CCa, SNa, RCa, R3a = [al(series[k]) for k in ['VL', 'VS', 'TL', 'CCI', 'SNAP', 'RCLM', 'R3']]
print(f"\n=== COMMON WINDOW {I.min()} .. {I.max()}  ({len(I)} trading days) ===")
for k, a in [('VL', VLa), ('VS', VSa), ('TL', TLa), ('CCI', CCa), ('SNAP', SNa), ('RCLM', RCa), ('R3', R3a)]:
    print(f"  {k:5s} active={int((a != 0).sum()):>4}  net(window)=${a.sum():>9,.0f}")

BASE = VLa + VSa + TLa
port = {
    '1 BASELINE (mod V9)': BASE,
    '2 +CCI': BASE + CCa,
    '3 +Snapback': BASE + SNa,
    '4 +TLB Reclaim': BASE + RCa,
    '5 +R3 re-entry': BASE + R3a,
    '6 +Snap+CCI': BASE + SNa + CCa,
    '7 +ALL FOUR': BASE + CCa + SNa + RCa + R3a,
    '8 +Snap+CCI+Reclaim': BASE + SNa + CCa + RCa,
    '9 +CCI+Reclaim': BASE + CCa + RCa,
    '10 +CCI+Snap+R3': BASE + CCa + SNa + R3a,
    '11 +CCI+R3': BASE + CCa + R3a,
}
ANN = 252.0


def sharpe(x):
    x = np.asarray(x, float)
    sd = x.std(ddof=1)
    return x.mean() / sd * np.sqrt(ANN) if sd > 0 else float('nan')


def maxdd(x):
    cu = np.cumsum(np.asarray(x, float))
    pk = np.maximum.accumulate(cu)
    return (cu - pk).min()


def retdd(x):
    dd = maxdd(x)
    return np.sum(x) / abs(dd) if dd < 0 else float('nan')


def ann_usd(x):
    x = np.asarray(x, float)
    return x.mean() * ANN


yrs = pd.Series(pd.to_datetime(I).year, index=I)
mons = pd.Series(pd.to_datetime(I).to_period('M').astype(str), index=I)

print("\n" + "=" * 122)
print(f"{'PORTFOLIO':22} {'net$':>9} {'ann$':>9} {'Sharpe':>7} {'maxDD$':>9} {'ret/DD':>7} | {'dNet%':>7} {'dDD%':>7} {'dRetDD':>7} {'dSharpe':>8}")
print("=" * 122)
b = port['1 BASELINE (mod V9)']
bnet = b.sum(); bdd = maxdd(b); brd = retdd(b); bsh = sharpe(b)
for name, x in port.items():
    net = x.sum(); dd = maxdd(x); rd = retdd(x); sh = sharpe(x); an = ann_usd(x)
    dnet = (net - bnet) / abs(bnet) * 100 if bnet else 0
    ddd = (abs(dd) - abs(bdd)) / abs(bdd) * 100 if bdd else 0
    drd = rd - brd; dsh = sh - bsh
    if 'BASELINE' in name:
        print(f"{name:22} {net:>9,.0f} {an:>9,.0f} {sh:>7.2f} {dd:>9,.0f} {rd:>7.2f} | {'--':>7} {'--':>7} {'--':>7} {'--':>8}")
    else:
        print(f"{name:22} {net:>9,.0f} {an:>9,.0f} {sh:>7.2f} {dd:>9,.0f} {rd:>7.2f} | {dnet:>+6.1f}% {ddd:>+6.1f}% {drd:>+7.2f} {dsh:>+8.2f}")

print("\n" + "=" * 90)
print("YEARLY net $ (partial years flagged)")
print("=" * 90)
ylist = sorted(set(yrs))
counts = yrs.value_counts()
partial = {y: (counts[y] < 180) for y in ylist}
print(f"{'PORTFOLIO':22} " + "  ".join(f"{y:>9}" for y in ylist))
for name, x in port.items():
    cells = [f"{x[yrs == y].sum():>9,.0f}" for y in ylist]
    print(f"{name:22} " + "  ".join(cells))
print("  partial-year coverage:", ", ".join(f"{y}({counts[y]}d)" for y in ylist if partial[y]))

print("\n" + "=" * 100)
print("MONTHLY profile (over common window)")
print("=" * 100)
print(f"{'PORTFOLIO':22} {'%posMo':>7} {'worst$':>9} {'best$':>9} {'avgMo$':>9} {'moStd$':>9} {'nMo':>4}")
monthly_tbl = {}
for name, x in port.items():
    m = x.groupby(mons).sum()
    monthly_tbl[name] = m
    pos = (m > 0).mean() * 100
    print(f"{name:22} {pos:>6.0f}% {m.min():>9,.0f} {m.max():>9,.0f} {m.mean():>9,.0f} {m.std():>9,.0f} {len(m):>4}")

print("\n" + "=" * 100)
print("MONTHLY net $: BASELINE vs +ALL FOUR")
print("=" * 100)
mb = monthly_tbl['1 BASELINE (mod V9)']
ma = monthly_tbl['7 +ALL FOUR']
print(f"{'month':9} {'BASE$':>10} {'ALL4$':>10} {'delta$':>10}")
for mo in mb.index:
    print(f"{mo:9} {mb[mo]:>10,.0f} {ma[mo]:>10,.0f} {ma[mo] - mb[mo]:>10,.0f}")

print("\n" + "=" * 70)
print("DAILY $ CORRELATION vs BASELINE (all-days) + own vol")
print("=" * 70)
for k, a in [('CCI', CCa), ('SNAP', SNa), ('RCLM', RCa), ('R3', R3a)]:
    cc = np.corrcoef(np.asarray(a, float), np.asarray(b, float))[0, 1]
    print(f"  {k:6s} corr_vs_base={cc:+.3f}  own dailySD=${a.std():.1f}  own net=${a.sum():,.0f}  activeDays={int((a!=0).sum())}")

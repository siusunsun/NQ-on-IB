"""_hist_integ2.py - supplementary integrity: old-vs-new data quality comparison, roll audit."""
import glob, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
DIR = '/root/tlbrc_paper/ref/data_full/NQ/'

def load_csv(path):
    df = pd.read_csv(path); df.columns = [c.strip().lower() for c in df.columns]
    tc = next(c for c in ('time', 'timestamp', 'date', 'datetime') if c in df.columns)
    df[tc] = pd.to_datetime(df[tc], utc=True, errors='coerce')
    df = df.dropna(subset=[tc]).set_index(tc).sort_index()
    if 'volume' not in df.columns: df['volume'] = 0.0
    return df[['open', 'high', 'low', 'close', 'volume']].astype(float)

df = pd.concat([load_csv(f) for f in sorted(glob.glob(DIR + '*.csv'))]).sort_index()
df = df[~df.index.duplicated(keep='last')].sort_index()

print("=== OHLC SANITY ===")
bad_hl = (df['high'] < df['low']).sum()
bad_o = ((df['open'] > df['high']) | (df['open'] < df['low'])).sum()
bad_c = ((df['close'] > df['high']) | (df['close'] < df['low'])).sum()
print(f"high<low: {bad_hl}   open outside HL: {bad_o}   close outside HL: {bad_c}")
print(f"nonpositive prices: {(df[['open','high','low','close']] <= 0).values.sum()}")
print(f"zero-volume bars: {(df['volume'] <= 0).sum():,} ({(df['volume']<=0).mean()*100:.2f}%)")
print(f"flat bars (h==l): {(df['high']==df['low']).sum():,} ({(df['high']==df['low']).mean()*100:.2f}%)")

print("\n=== PER-YEAR DATA CHARACTER (old 2021-2023 vs live 2024-2026) ===")
y = df.index.year
rows = []
for yy in sorted(set(y)):
    d = df[y == yy]
    rng = (d['high'] - d['low'])
    rows.append(dict(year=yy, bars=len(d), med_vol=d['volume'].median(), mean_vol=round(d['volume'].mean(), 1),
                     med_range_pt=rng.median(), p99_range=round(rng.quantile(0.99), 2),
                     zero_vol_pct=round((d['volume'] <= 0).mean() * 100, 2),
                     flat_pct=round((d['high'] == d['low']).mean() * 100, 2),
                     px_min=d['low'].min(), px_max=d['high'].max()))
print(pd.DataFrame(rows).to_string(index=False))

print("\n=== KNOWN-LEVEL SPOT CHECKS (NQ front month) ===")
for label, day in [('2021-11-22 ATH area', '2021-11-22'), ('2022-01-24 selloff', '2022-01-24'),
                   ('2022-06-16 bear low area', '2022-06-16'), ('2022-10-13 CPI reversal', '2022-10-13'),
                   ('2023-01-06', '2023-01-06'), ('2023-07-19', '2023-07-19')]:
    d = df[df.index.tz_convert(ET).date.astype(str) == day]
    if len(d):
        print(f"  {label:26} {day}: low={d['low'].min():.2f} high={d['high'].max():.2f} "
              f"close={d['close'].iloc[-1]:.2f} bars={len(d)}")
    else:
        print(f"  {label:26} {day}: NO DATA")

print("\n=== QUARTERLY ROLL AUDIT (largest close->next-open jump per roll quarter) ===")
c = df['close']; o = df['open']
step = (o.shift(-1) - c)
et = df.index.tz_convert(ET)
for yy in sorted(set(y)):
    for mm in (3, 6, 9, 12):
        m = (et.year == yy) & (et.month == mm) & (et.day <= 22)
        if m.sum() == 0: continue
        s = step[m].abs()
        i = s.idxmax()
        print(f"  {yy}-{mm:02d}: max |jump| = {s.max():7.2f}pt at {i} ET={i.tz_convert(ET)}  "
              f"(close {c.loc[i]:.2f} -> open {o.iloc[df.index.get_loc(i)+1]:.2f})")

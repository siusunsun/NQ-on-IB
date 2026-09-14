"""_hist_integrity.py - STEP 0 data integrity on combined full-history NQ dir. READ-ONLY."""
import glob, sys, warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
DIR='/root/tlbrc_paper/ref/data_full/NQ/'
LIVE='/root/v9/data_1m/NQ/'

def load_csv(path):
    df=pd.read_csv(path); df.columns=[c.strip().lower() for c in df.columns]
    tc=next(c for c in ('time','timestamp','date','datetime') if c in df.columns)
    if pd.api.types.is_numeric_dtype(df[tc]):
        df[tc]=pd.to_datetime(df[tc],utc=True,unit='s',errors='coerce')
    else:
        df[tc]=pd.to_datetime(df[tc],utc=True,errors='coerce')
    df=df.dropna(subset=[tc]).set_index(tc).sort_index()
    if 'volume' not in df.columns: df['volume']=0.0
    return df[['open','high','low','close','volume']].astype(float)

files=sorted(glob.glob(DIR+'*.csv'))
print(f'files={len(files)}')
raw=pd.concat([load_csv(f) for f in files]).sort_index(kind="mergesort")  # 2026-09-14: STABLE sort
print(f'raw rows (pre-dedupe)={len(raw):,}')
dups=raw.index.duplicated(keep='last')
print(f'(b) DUPLICATE timestamps removed = {dups.sum():,}')
df=raw[~dups].sort_index()
print(f'final rows={len(df):,}  span {df.index[0]} -> {df.index[-1]}  ({(df.index[-1]-df.index[0]).days/365.25:.2f} yrs)')
print(f'monotonic increasing: {df.index.is_monotonic_increasing}')

# (a) seam
print('\n=== (a) SEAM at 2023-12-22 ===')
seam=pd.Timestamp('2023-12-22 04:33',tz='UTC')
w=df.loc[seam-pd.Timedelta(minutes=4):seam+pd.Timedelta(minutes=4)]
print(w.to_string())
# where did the old file end / new begin
old=load_csv(DIR+'NQ_1min_2021_2023.csv')
print(f'old file: {old.index[0]} -> {old.index[-1]}  rows={len(old):,}')
newfiles=[f for f in files if 'NQ_1min_2021_2023' not in f]
new=pd.concat([load_csv(f) for f in newfiles]).sort_index()
new=new[~new.index.duplicated(keep='last')]
print(f'live files: {new.index[0]} -> {new.index[-1]}  rows={len(new):,}')
ov=old.index.intersection(new.index)
print(f'OVERLAP between old and live sets: {len(ov)} bars')
if len(ov):
    cmpd=pd.DataFrame({'old_c':old.loc[ov,'close'],'new_c':new.loc[ov,'close']})
    cmpd['d']=(cmpd.old_c-cmpd.new_c).abs()
    print(f'  overlap close diff: max={cmpd.d.max():.2f} mean={cmpd.d.mean():.4f}  nonzero={(cmpd.d>0.01).sum()}')
print(f'gap old_end -> live_start = {new.index[0]-old.index[-1]}')
print(f'price old_end close={old["close"].iloc[-1]}  live_start open={new["open"].iloc[0]}  jump={new["open"].iloc[0]-old["close"].iloc[-1]:+.2f}')

# (c) bars per month
print('\n=== (c) BARS PER MONTH ===')
m=df.groupby([df.index.year,df.index.month]).size()
et=df.index.tz_convert(ET)
tdays=pd.Series(et.date,index=df.index).groupby([df.index.year,df.index.month]).nunique()
out=pd.DataFrame({'bars':m,'et_days':tdays})
out['bars_per_day']=(out.bars/out.et_days).round(0)
print(out.to_string())

# gaps
print('\n=== GAPS > 6h ===')
d=df.index.to_series().diff().dropna()
big=d[d>pd.Timedelta(hours=6)]
print(f'count={len(big)}')
for t,g in big.items():
    print(f'  {g} ending {t}  ({t.tz_convert(ET)} ET)')

# (d) contract roll discontinuities: bar-to-bar jumps
print('\n=== (d) BAR-TO-BAR PRICE DISCONTINUITIES ===')
c=df['close']; o=df['open']
step=(o.shift(-1)-c).abs()  # close[i] -> open[i+1]
big2=step[step>100].dropna()
print(f'|close->next open| > 100pt : {len(big2)} occurrences')
tbl=pd.DataFrame({'ts':big2.index,'jump':big2.values,
                  'prev_close':c.reindex(big2.index).values,
                  'gap_min':(pd.Series(df.index).diff().shift(-1).reindex(pd.Series(df.index)[df.index.isin(big2.index)].index).dt.total_seconds()/60).values if False else np.nan})
for ts,j in big2.sort_values(ascending=False).head(40).items():
    i=df.index.get_loc(ts)
    nxt=df.index[i+1] if i+1<len(df) else None
    dt=(nxt-ts) if nxt is not None else None
    print(f'  {ts} ET={ts.tz_convert(ET)}  close={c.iloc[i]:.2f} -> next open={o.iloc[i+1]:.2f}  jump={j:+.1f}  dt={dt}')
# roll windows: mar/jun/sep/dec 2nd thursday-ish (roll ~ 8 days before 3rd friday)
print('\n=== jumps>100pt grouped by month ===')
print(big2.groupby([big2.index.year,big2.index.month]).size().to_string())

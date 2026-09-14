"""Targeted: does a PRE-roll timestamp appear inside a POST-roll seam file, and at which price?
NQ Jun-2026 roll: M6 -> U6 around 2026-06-18/19."""
import glob, os, pandas as pd, numpy as np
D='/root/v9/data_1m/NQ/'
ROLL=pd.Timestamp('2026-06-18',tz='UTC')
pre_in_post={}
for f in sorted(glob.glob(D+'NQ_1min_seam_*.csv')):
    fdate=pd.Timestamp(os.path.basename(f)[14:24],tz='UTC')
    if fdate <= ROLL: continue                       # only files WRITTEN after the roll
    d=pd.read_csv(f,usecols=['time','close'])
    d['time']=pd.to_datetime(d['time'],utc=True,errors='coerce')
    pre=d[d['time'] < ROLL]                          # bars from BEFORE the roll
    if len(pre): pre_in_post[os.path.basename(f)]=(len(pre), pre['time'].min(), pre['time'].max(), pre['close'].mean())
print('post-roll files containing PRE-roll bars:', len(pre_in_post))
for k,v in list(pre_in_post.items())[:6]:
    print(f'  {k}: {v[0]} bars {v[1]} .. {v[2]}  meanClose {v[3]:.1f}')
# reference: what did a PRE-roll file say for the same period?
ref=[]
for f in sorted(glob.glob(D+'NQ_1min_seam_*.csv')):
    fdate=pd.Timestamp(os.path.basename(f)[14:24],tz='UTC')
    if fdate > ROLL: continue
    d=pd.read_csv(f,usecols=['time','close']); d['time']=pd.to_datetime(d['time'],utc=True,errors='coerce')
    p=d[d['time']<ROLL]
    if len(p): ref.append((os.path.basename(f),len(p),p['close'].mean()))
print('pre-roll files (written before the roll):')
for k,n,m in ref[:6]: print(f'  {k}: {n} bars meanClose {m:.1f}')
# price level across the roll in the merged series
allf=[]
for f in sorted(glob.glob(D+'*.csv')):
    try:
        d=pd.read_csv(f,usecols=['time','close']); d['time']=pd.to_datetime(d['time'],utc=True,errors='coerce'); allf.append(d.dropna())
    except Exception: pass
m=pd.concat(allf).drop_duplicates('time',keep='first').sort_values('time')
w=m[(m['time']>ROLL-pd.Timedelta(days=3))&(m['time']<ROLL+pd.Timedelta(days=3))]
day=w.set_index('time')['close'].resample('1D').agg(['first','last','count'])
print('\ndaily close around the roll (merged series):'); print(day.dropna().to_string())

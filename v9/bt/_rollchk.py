"""Detect roll contamination: same timestamp appearing in >1 seam file with
materially different prices (= a pre-roll bar re-fetched at the new contract's level)."""
import glob, os, collections
import pandas as pd, numpy as np
D='/root/v9/data_1m/NQ/'
files=sorted(glob.glob(D+'NQ_1min_seam_*.csv'))
print(f'seam files: {len(files)}  ({os.path.basename(files[0])} .. {os.path.basename(files[-1])})')
# load compactly: time + close only
frames={}
for f in files:
    try:
        d=pd.read_csv(f,usecols=['time','close'])
        d['time']=pd.to_datetime(d['time'],utc=True,errors='coerce')
        frames[os.path.basename(f)]=d.dropna()
    except Exception as e: print('  skip',os.path.basename(f),e)
# build timestamp -> {file: close}
acc=collections.defaultdict(dict)
for name,d in frames.items():
    for t,c in zip(d['time'].values, d['close'].values):
        acc[t][name]=c
dups={t:v for t,v in acc.items() if len(v)>1}
print(f'timestamps present in >1 seam file: {len(dups):,}')
bad=[]
for t,v in dups.items():
    vals=list(v.values())
    spread=max(vals)-min(vals)
    if spread>5:      # >5 pts on the SAME minute = different contracts
        bad.append((t,spread,v))
print(f'  of those, disagreeing by >5 pts: {len(bad):,}')
if bad:
    bad.sort(key=lambda x:-x[1])
    print('  WORST 8:')
    for t,s,v in bad[:8]:
        print(f'    {pd.Timestamp(t)}  spread {s:.2f}  ', {k[-14:]:round(float(x),2) for k,x in v.items()})
    yrs=collections.Counter(pd.Timestamp(t).year for t,_,_ in bad)
    print('  by year:',dict(yrs))
else:
    print('  NO cross-file price disagreement -> no roll contamination in seam files')

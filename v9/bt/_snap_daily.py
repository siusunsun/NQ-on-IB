import glob, sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np, pandas as pd
NQ_DIR='/root/v9/data_1m/NQ/'
fs=sorted(glob.glob(NQ_DIR+'*.csv'))
parts=[]
for f in fs:
    try:
        d=pd.read_csv(f, usecols=['time','close'])
        parts.append(d)
    except Exception as e:
        print('skip',f,e)
d=pd.concat(parts, ignore_index=True)
# time may be datetime string or unix seconds
t=pd.to_datetime(d['time'], utc=True, errors='coerce')
if t.isna().mean()>0.5:
    t=pd.to_datetime(d['time'], utc=True, unit='s', errors='coerce')
d['time']=t
d=d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')
day=d['close'].resample('1D').last().dropna()
day.index=day.index.tz_localize(None)
print('1-min rows:',len(d),' span',d.index[0],'->',d.index[-1])
print('daily bars:',len(day),' span',day.index[0].date(),'->',day.index[-1].date())
# monthly counts to spot gaps
mc=day.groupby([day.index.year,day.index.month]).size()
print('--- trading-day count per month ---')
print(mc.to_string())
# gaps > 4 days
gaps=day.index.to_series().diff().dt.days
big=gaps[gaps>4]
print('--- gaps > 4 calendar days ---')
for ix,g in big.items():
    print(ix.date(), int(g),'days')
day.to_pickle('/root/v9/bt/_snap_daily_mid.pkl')
print('saved _snap_daily_mid.pkl')

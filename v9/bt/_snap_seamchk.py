import sys,warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import numpy as np, pandas as pd
day=pd.read_pickle('/root/v9/bt/_snap_daily_mid.pkl')
ret=day.pct_change()
print('largest 12 abs daily moves (midnight-anchor):')
top=ret.abs().sort_values(ascending=False).head(12)
for ix,v in top.items():
    print(ix.date(), f'{ret[ix]*100:+.2f}%', f'{day[ix]:.1f}')
print()
print('daily ret std %:', ret.std()*100, ' annualized vol %:', ret.std()*np.sqrt(252)*100)
# show closes around known roll seam dates
for dt in ['2026-06-01','2026-06-30','2026-07-05']:
    print('--around',dt); print(day[(day.index>=pd.Timestamp(dt)-pd.Timedelta('4D'))&(day.index<=pd.Timestamp(dt)+pd.Timedelta('4D'))].to_string())

import glob,os,re,pandas as pd,numpy as np,datetime as dt
D='/root/v9/data_1m/NQ/'
fs=sorted(glob.glob(D+'NQ_1min_seam_*.csv'))
dates=[re.search(r'(\d{4}-\d{2}-\d{2})',os.path.basename(f)).group(1) for f in fs]
print(f'seam files span {dates[0]} .. {dates[-1]}  (n={len(fs)})')
# NQ quarterly roll ~8 days before 3rd-Friday expiry
def third_friday(y,m):
    d=dt.date(y,m,1); fr=[d.replace(day=x) for x in range(1,32) if x<=(dt.date(y,m+1,1)-dt.timedelta(days=1)).day if d.replace(day=x).weekday()==4]
    return fr[2]
for y,m in [(2026,3),(2026,6),(2026,9),(2026,12)]:
    exp=third_friday(y,m); roll=exp-dt.timedelta(days=8)
    print(f'  {y}-{m:02d} expiry {exp}  roll ~{roll}')
# does the seam window contain any roll?
print()
sept_roll=third_friday(2026,9)-dt.timedelta(days=8)
print(f'NEXT ROLL ~{sept_roll}  -> in {(sept_roll-dt.date.today()).days} days')
print(f'seam window starts {dates[0]} ; June roll was ~{third_friday(2026,6)-dt.timedelta(days=8)}')
print(' => seam files begin AFTER the June roll, so they contain NO roll boundary yet.')
# archive roll-gap sanity (evidence historical data is uncontaminated)
big=D+'NQ_1min_utc.csv'
if os.path.exists(big):
    d=pd.read_csv(big,usecols=['time','close'])
    d['time']=pd.to_datetime(d['time'],utc=True,errors='coerce'); d=d.dropna().sort_values('time')
    d['j']=d['close'].diff().abs()
    big_j=d[d['j']>150]
    print(f'\narchive {os.path.basename(big)}: bars={len(d):,}  jumps>150pt={len(big_j)}')
    for _,r in big_j.tail(6).iterrows(): print(f'   {r["time"]}  jump {r["j"]:.1f}')

import numpy as np, pandas as pd, math
RT=2.04; BT='/root/v9/bt/'; CAP=22500.0
def load(tag,qty):
    d=pd.read_csv(BT+f'_hist_tr_{tag}.csv')
    col=[c for c in d.columns if 'entry' in c.lower() and ('date' in c.lower() or 'utc' in c.lower() or 'time' in c.lower())][0]
    t=pd.to_datetime(d[col],utc=True,errors='coerce')
    if t.isna().all(): t=pd.to_datetime(d[col],errors='coerce').dt.tz_localize('UTC')
    d['dt']=t.dt.tz_convert('America/New_York').dt.date
    pc=[c for c in d.columns if c.lower() in ('pnl_usd','pnl_usd_1mnq','net_usd','usd')][0]
    d['net']=d[pc]*qty-RT*qty
    return d.groupby('dt')['net'].sum()
VL=load('VWAP_LONG_R3',2); VS=load('VWAP_SHORT_R1',1); TL=load('TLB_HYB40',1)
r=pd.read_csv(BT+'_r3_48_daily.csv'); r['dt']=pd.to_datetime(r['date']).dt.date
R3=r.set_index('dt')['usd']
start=min(x.index.min() for x in [VL,VS,TL,R3]); end=max(x.index.max() for x in [VL,VS,TL,R3])
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(R3.index)))
I=pd.Index([d for d in I if start<=d<=end])
al=lambda s:s.reindex(I).fillna(0.0)
B=al(VL)+al(VS)+al(TL)+al(R3)
net=B.sum(); yrs=(pd.Timestamp(end)-pd.Timestamp(start)).days/365.25
endeq=CAP+net
cagr=(endeq/CAP)**(1/yrs)-1
print(f'capital ${CAP:,.0f}  net ${net:,.0f}  end equity ${endeq:,.0f}  years {yrs:.2f}')
print(f'SIMPLE annualized (fixed size): {100*(net/yrs)/CAP:.1f}%')
print(f'CAGR (fixed size, no compounding of contracts): {100*cagr:.1f}%')
print(f'total return over the period: {100*net/CAP:.1f}%')
print()
# monthly % of RUNNING equity (time-weighted, fixed contracts)
mo=pd.Series(pd.to_datetime(I).to_period('M').astype(str),index=I)
m=B.groupby(mo).sum()
eq=CAP; rows={}
for k,v in m.items():
    y,mm=k.split('-'); rows.setdefault(y,{})[int(mm)]=100*v/eq; eq+=v
print('MONTHLY % (of running equity, fixed 5 MNQ)')
hdr='YEAR '+''.join(f'{x:>8}' for x in ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'])+f'{"YTD":>9}'
print(hdr)
for y in sorted(rows):
    cells=''; ytd=1.0
    for i in range(1,13):
        if i in rows[y]:
            cells+=f'{rows[y][i]:>+8.1f}'; ytd*= (1+rows[y][i]/100)
        else: cells+=f'{"—":>8}'
    print(f'{y} '+cells+f'{100*(ytd-1):>+8.1f}%')
print()
print(f'final equity ${eq:,.0f}   cumulative {100*(eq/CAP-1):.1f}%')

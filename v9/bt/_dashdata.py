import json, pandas as pd, numpy as np
BT='/root/v9/bt/'; RT=2.04
QTY={'VWAP_LONG_R3':2,'VWAP_SHORT_R1':1,'TLB_HYB40':1,'R3_RE':1}
rows=[]
for tag in ('VWAP_LONG_R3','VWAP_SHORT_R1','TLB_HYB40'):
    d=pd.read_csv(BT+f'_hist_tr_{tag}.csv')
    q=QTY[tag]
    for _,r in d.iterrows():
        rows.append(dict(sleeve=tag, exit=pd.to_datetime(r['exit_utc'],utc=True),
            date=str(pd.to_datetime(r['entry_et']).date()),
            direction='Long' if str(r['direction']).lower().startswith('l') else 'Short',
            entry=float(r['entry_px']), ex=float(r['exit_px']),
            pnl=float(r['pnl_usd'])*q-RT*q, R=float(r['R'])))
d=pd.read_csv(BT+'_r3v_fires.csv')
for _,r in d.iterrows():
    rows.append(dict(sleeve='R3_RE', exit=pd.to_datetime(r['exit_utc'],utc=True),
        date=str(pd.to_datetime(r['entry_date_et']).date()), direction='Long',
        entry=float(r['fill']), ex=float(r['exit_px']),
        pnl=float(r['pnl_usd_1mnq'])-RT, R=float(r['R'])))
df=pd.DataFrame(rows).sort_values('exit')
last=df.tail(5)
def money(v): return ('+$' if v>=0 else '-$')+f'{abs(v):,.0f}'
trades=[]
for _,r in last.iterrows():
    trades.append({"date":r['date'],"direction":r['direction'],
        "entry":f"{r['entry']:,.0f}","exit":f"{r['ex']:,.0f}",
        "pnl":money(r['pnl']),"pnlNum":round(float(r['pnl']),2),
        "rMultiple":f"{r['R']:+.1f}R","sleeve":r['sleeve']})
out={"lastUpdated":pd.Timestamp.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
     "basis":"THEORETICAL (backtest signals, not live fills) - MNQ $2/pt at deployed size",
     "recentTrades":trades,"positions":[]}
print(json.dumps(out,indent=2))
print('\n--- window of last 5:', last['exit'].min(), '->', last['exit'].max())

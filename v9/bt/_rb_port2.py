# -*- coding: utf-8 -*-
"""_rb_port2.py  Value-add at the REAL minimum lot (1 MNQ), plus the LOOSE CCI cell. READ-ONLY."""
import sys, itertools, warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8',errors='replace'); sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _rb_lib import book_stats
BT='/root/v9/bt/'; NY='America/New_York'
def live(tag):
    d=pd.read_csv(BT+'_hist_tr_%s.csv'%tag)
    d['dt']=pd.to_datetime(d['entry_utc'],utc=True).dt.tz_convert(NY).dt.date
    return d.groupby('dt')['pnl_usd'].sum()
VL,VS,TL,RE=live('VWAP_LONG_R3'),live('VWAP_SHORT_R1'),live('TLB_HYB40'),live('R3_RE')
start=min(x.index.min() for x in [VL,VS,TL,RE]); end=max(x.index.max() for x in [VL,VS,TL,RE])
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(RE.index)))
I=pd.Index([d for d in I if start<=d<=end])
al=lambda s: s.reindex(I).fillna(0.0)
BOOK=al(VL)*2+al(VS)*2+al(TL)+al(RE)
b0=book_stats(BOOK.values)
snap=pd.read_csv(BT+'_rb_snap_daily.csv'); snap['dt']=pd.to_datetime(snap['date']).dt.date
SNAP=al(snap.groupby('dt')['pnl_usd'].sum())
def trades(f,dc,vc,mult=1.0,tz=False):
    d=pd.read_csv(BT+f)
    d['dt']=(pd.to_datetime(d[dc],utc=True).dt.tz_convert(NY).dt.date if tz else pd.to_datetime(d[dc]).dt.date)
    return al(d.groupby('dt').apply(lambda g: (g[vc[0]]*(g[vc[1]] if len(vc)>1 else 1)).sum())*mult)
CCI=al(pd.read_csv(BT+'_rb_cci_trades.csv').assign(dt=lambda d: pd.to_datetime(d['date']).dt.date).groupby('dt')['pts'].sum()*2.0)
CCIL=al(pd.read_csv(BT+'_rb_cci_loose_trades.csv').assign(dt=lambda d: pd.to_datetime(d['date']).dt.date).groupby('dt')['pts'].sum()*2.0)
tl=pd.read_csv(BT+'_rb_tlbrc_trades.csv')
tl['dt']=pd.to_datetime(tl['entry_t'],utc=True).dt.tz_convert(NY).dt.date
TLBRC=al((tl.assign(u=tl['r']*tl['risk']*2.0)).groupby('dt')['u'].sum())
C={'SNAPBACK':SNAP,'CCI_AM_deployed':CCI,'CCI_AM_loose':CCIL,'TLB_RECLAIM':TLBRC}
print('BASE book: net $%.0f Sharpe %.2f maxDD $%.0f ret/DD %.2f   (live size = 1x = 2/2/1/1 MNQ)'%(
    b0['net'],b0['sharpe'],b0['maxdd'],b0['retdd']))
print('\nSTANDALONE (1 MNQ), same window:')
for k,v in C.items():
    s=book_stats(v.values)
    print('  %-16s net $%7.0f  Sharpe %5.2f  maxDD $%7.0f  ret/DD %5.2f  daily sd $%5.0f  corr(book) %+.3f'%(
        k,s['net'],s['sharpe'],s['maxdd'],s['retdd'],v.values.std(ddof=1),np.corrcoef(v.values,BOOK.values)[0,1]))
print('\nADD 1 MNQ (the real minimum) TO THE LIVE 1x BOOK:')
print('  %-30s %9s %8s %10s %8s %9s %9s %10s'%('addition','net$','Sharpe','maxDD$','ret/DD','dSharpe','dRetDD','dMaxDD'))
print('  %-30s %9.0f %8.2f %10.0f %8.2f'%('(baseline)',b0['net'],b0['sharpe'],b0['maxdd'],b0['retdd']))
def show(name,v):
    s=book_stats((BOOK+v).values)
    print('  %-30s %9.0f %8.2f %10.0f %8.2f %+9.2f %+9.2f %+10.0f'%(
        name,s['net'],s['sharpe'],s['maxdd'],s['retdd'],s['sharpe']-b0['sharpe'],s['retdd']-b0['retdd'],s['maxdd']-b0['maxdd']))
    return s
for k,v in C.items(): show(k,v)
print('  -- pairs / triples, 1 MNQ each --')
for r in (2,3,4):
    for combo in itertools.combinations(C,r):
        if 'CCI_AM_deployed' in combo and 'CCI_AM_loose' in combo: continue
        show('+'.join(x.replace('CCI_AM_','CCI-') for x in combo), sum(C[x] for x in combo))
print('\nHALF-SAMPLE ROBUSTNESS of the 1-MNQ addition (incremental Sharpe):')
mid=len(I)//2
for k,v in C.items():
    o=[]
    for lbl,sl in (('H1',slice(0,mid)),('H2',slice(mid,None))):
        a=book_stats(BOOK.values[sl]); b=book_stats((BOOK+v).values[sl])
        o.append('%s %.2f->%.2f (%+.2f)'%(lbl,a['sharpe'],b['sharpe'],b['sharpe']-a['sharpe']))
    print('  %-16s '%k+'   '.join(o))
print('\nPER-YEAR incremental Sharpe of the 1-MNQ addition:')
yrs=sorted(set(pd.to_datetime(I).year))
print('  %-16s'%'addition'+''.join('%16d'%y for y in yrs))
for k,v in C.items():
    row='  %-16s'%k
    for y in yrs:
        m=pd.to_datetime(I).year==y
        a=book_stats(BOOK.values[m]); b=book_stats((BOOK+v).values[m])
        row+='%9.0f/%+6.2f'%(b['net']-a['net'],b['sharpe']-a['sharpe'])
    print(row)
print('\nCORRELATION per year, incl. loose CCI:')
print('  %-16s'%'cand'+''.join('%9d'%y for y in yrs)+'%9s'%'FULL')
for k,v in C.items():
    row='  %-16s'%k
    for y in yrs:
        m=pd.to_datetime(I).year==y
        a,b=v.values[m],BOOK.values[m]
        row+='%9.3f'%(np.corrcoef(a,b)[0,1] if a.std()>0 and b.std()>0 else np.nan)
    print(row+'%9.3f'%np.corrcoef(v.values,BOOK.values)[0,1])
print('\nWorst 6 book drawdown episodes, baseline vs +TLB_RECLAIM(1) vs +CCI_loose(1):')
def dds(x,lbl):
    cu=np.cumsum(np.asarray(x,float)); pk=np.maximum.accumulate(cu); d=cu-pk; dts=list(I); eps=[];i=0
    while i<len(d):
        if d[i]<0:
            j=i
            while j<len(d) and d[j]<0: j+=1
            seg=d[i:j]; eps.append((float(seg.min()),str(dts[i]),str(dts[i+int(np.argmin(seg))]),int(j-i))); i=j
        else: i+=1
    eps=sorted(eps)[:6]
    print('  %-22s '%lbl+' | '.join('$%.0f %s (%dd)'%(e[0],e[2],e[3]) for e in eps))
dds(BOOK.values,'baseline')
dds((BOOK+TLBRC).values,'+TLB_RECLAIM 1MNQ')
dds((BOOK+CCIL).values,'+CCI_loose 1MNQ')
dds((BOOK+TLBRC+CCIL).values,'+both 1MNQ')

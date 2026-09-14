# -*- coding: utf-8 -*-
"""_rb_port.py  Correlation vs the LIVE book + portfolio value-add for the 3 candidates.
READ-ONLY. All $ at 1 MNQ = $2/pt unless a scale factor is applied."""
import sys, itertools, warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8', errors='replace'); sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _rb_lib import book_stats
BT='/root/v9/bt/'
NY='America/New_York'

def live(tag):
    d=pd.read_csv(BT+'_hist_tr_%s.csv'%tag)
    d['dt']=pd.to_datetime(d['entry_utc'],utc=True).dt.tz_convert(NY).dt.date
    return d.groupby('dt')['pnl_usd'].sum()

VL,VS,TL,RE=live('VWAP_LONG_R3'),live('VWAP_SHORT_R1'),live('TLB_HYB40'),live('R3_RE')
start=min(x.index.min() for x in [VL,VS,TL,RE]); end=max(x.index.max() for x in [VL,VS,TL,RE])
I=pd.Index(sorted(set(pd.bdate_range(start,end).date)|set(VL.index)|set(VS.index)|set(TL.index)|set(RE.index)))
I=pd.Index([d for d in I if start<=d<=end])
al=lambda s: s.reindex(I).fillna(0.0)
VLa,VSa,TLa,REa=al(VL),al(VS),al(TL),al(RE)
BOOK=VLa*2+VSa*2+TLa+REa
print('LIVE BOOK window %s .. %s  (%d days)'%(I.min(),I.max(),len(I)))
b0=book_stats(BOOK.values)
print('  baseline: net $%.0f  Sharpe %.2f  maxDD $%.0f  ret/DD %.2f  ann $%.0f'%(
    b0['net'],b0['sharpe'],b0['maxdd'],b0['retdd'],b0['ann']))

# ---- candidates, daily $ at 1 MNQ ----
snap=pd.read_csv(BT+'_rb_snap_daily.csv')
snap['dt']=pd.to_datetime(snap['date']).dt.date
SNAP=al(snap.groupby('dt')['pnl_usd'].sum())
cci=pd.read_csv(BT+'_rb_cci_trades.csv')
cci['dt']=pd.to_datetime(cci['date']).dt.date
CCI=al(cci.groupby('dt')['pts'].sum()*2.0)
tlb=pd.read_csv(BT+'_rb_tlbrc_trades.csv')
tlb['dt']=pd.to_datetime(tlb['entry_t'],utc=True).dt.tz_convert(NY).dt.date
tlb['usd']=tlb['r']*tlb['risk']*2.0
TLBRC=al(tlb.groupby('dt')['usd'].sum())
CANDS={'SNAPBACK':SNAP,'CCI_AM':CCI,'TLB_RECLAIM':TLBRC}
SLEEVES={'VWAP_R3':VLa,'VWAP_short':VSa,'TLB_hyb':TLa,'R3_re':REa}

print('\n  candidate raw stats over the SAME window, at 1 MNQ:')
for k,v in CANDS.items():
    s=book_stats(v.values); act=(v!=0).sum()
    print('    %-12s net $%8.0f  Sharpe %5.2f  maxDD $%8.0f  ret/DD %5.2f  ann $%7.0f  active days %d  daily sd $%.0f'%(
        k,s['net'],s['sharpe'],s['maxdd'],s['retdd'],s['ann'],act,v.values.std(ddof=1)))
print('    %-12s net $%8.0f  Sharpe %5.2f  maxDD $%8.0f  ret/DD %5.2f  ann $%7.0f  active days %d  daily sd $%.0f'%(
    'LIVE BOOK',b0['net'],b0['sharpe'],b0['maxdd'],b0['retdd'],b0['ann'],int((BOOK!=0).sum()),BOOK.values.std(ddof=1)))

print('\n'+'='*112); print('6. CORRELATION vs LIVE BOOK, PER YEAR (daily $, all business days, 0-filled)'); print('='*112)
yrs=sorted(set(pd.to_datetime(I).year))
hdr='  %-13s'%'candidate'+''.join('%9d'%y for y in yrs)+'%9s'%'FULL'
print(hdr)
for k,v in CANDS.items():
    row='  %-13s'%k
    for y in yrs:
        m=pd.to_datetime(I).year==y
        a,bb=v.values[m],BOOK.values[m]
        row+='%9.3f'%(np.corrcoef(a,bb)[0,1] if a.std()>0 and bb.std()>0 else np.nan)
    row+='%9.3f'%np.corrcoef(v.values,BOOK.values)[0,1]
    print(row)
print('\n  vs each LIVE SLEEVE (full sample):')
print('  %-13s'%'candidate'+''.join('%12s'%s for s in SLEEVES)+'%12s'%'BOOK')
for k,v in CANDS.items():
    print('  %-13s'%k+''.join('%12.3f'%np.corrcoef(v.values,s.values)[0,1] for s in SLEEVES.values())
          +'%12.3f'%np.corrcoef(v.values,BOOK.values)[0,1])
print('  candidate-vs-candidate:')
ck=list(CANDS)
for i in range(len(ck)):
    for j in range(i+1,len(ck)):
        print('    %-12s vs %-12s  %+.3f'%(ck[i],ck[j],np.corrcoef(CANDS[ck[i]].values,CANDS[ck[j]].values)[0,1]))
print('  overlap-days-only correlation (both sleeves active same day):')
for k,v in CANDS.items():
    m=(v.values!=0)&(BOOK.values!=0)
    print('    %-12s  n_overlap=%4d  corr %+.3f'%(k,m.sum(),np.corrcoef(v.values[m],BOOK.values[m])[0,1] if m.sum()>10 else np.nan))

print('\n'+'='*112); print('7. PORTFOLIO VALUE-ADD  (baseline = live four-sleeve book)'); print('='*112)
def add(v,scale):
    return book_stats((BOOK+v*scale).values)
print('  %-13s %6s %10s %8s %10s %8s %9s %9s'%('candidate','scale','net$','Sharpe','maxDD$','ret/DD','dSharpe','dRetDD'))
print('  baseline      %6s %10.0f %8.2f %10.0f %8.2f'%('-',b0['net'],b0['sharpe'],b0['maxdd'],b0['retdd']))
BESTS={}
for k,v in CANDS.items():
    for sc in (0.125,0.25,0.5,0.75,1.0,1.5,2.0):
        s=add(v,sc)
        print('  %-13s %6.3f %10.0f %8.2f %10.0f %8.2f %+9.2f %+9.2f'%(
            k,sc,s['net'],s['sharpe'],s['maxdd'],s['retdd'],s['sharpe']-b0['sharpe'],s['retdd']-b0['retdd']))
    # scale that maximises Sharpe
    grid=np.arange(0.05,2.55,0.05)
    sh=[add(v,x)['sharpe'] for x in grid]
    bi=int(np.argmax(sh)); BESTS[k]=grid[bi]
    print('     -> Sharpe-optimal scale %.2f (Sharpe %.2f, +%.2f)'%(grid[bi],sh[bi],sh[bi]-b0['sharpe']))
    print()

print('  COMBINATIONS (each at its Sharpe-optimal scale, and at a conservative 0.5x of that):')
for rr in (2,3):
    for combo in itertools.combinations(CANDS,rr):
        for damp,lbl in ((1.0,'opt'),(0.5,'half-opt')):
            add_v=sum(CANDS[c]*BESTS[c]*damp for c in combo)
            s=book_stats((BOOK+add_v).values)
            print('    %-38s %-9s net $%8.0f  Sharpe %.2f (%+.2f)  maxDD $%8.0f  ret/DD %.2f (%+.2f)'%(
                '+'.join(combo),lbl,s['net'],s['sharpe'],s['sharpe']-b0['sharpe'],s['maxdd'],s['retdd'],s['retdd']-b0['retdd']))
print('  ALL THREE:')
for damp,lbl in ((1.0,'opt'),(0.5,'half-opt'),(0.25,'quarter-opt')):
    add_v=sum(CANDS[c]*BESTS[c]*damp for c in CANDS)
    s=book_stats((BOOK+add_v).values)
    print('    %-38s %-9s net $%8.0f  Sharpe %.2f (%+.2f)  maxDD $%8.0f  ret/DD %.2f (%+.2f)'%(
        'SNAP+CCI+TLBRC',lbl,s['net'],s['sharpe'],s['sharpe']-b0['sharpe'],s['maxdd'],s['retdd'],s['retdd']-b0['retdd']))

print('\n  PER-YEAR value-add of each candidate at its Sharpe-optimal scale (net $ change and book Sharpe change):')
print('  %-13s %6s'%('candidate','scale')+''.join('%16d'%y for y in yrs))
for k,v in CANDS.items():
    sc=BESTS[k]; row='  %-13s %6.2f'%(k,sc)
    for y in yrs:
        m=(pd.to_datetime(I).year==y)
        b1=book_stats(BOOK.values[m]); b2=book_stats((BOOK+v*sc).values[m])
        row+='%9.0f/%+6.2f'%(b2['net']-b1['net'],b2['sharpe']-b1['sharpe'])
    print(row)

print('\n  ROBUSTNESS OF THE VALUE-ADD: incremental Sharpe computed on each half of the sample')
mid=len(I)//2
for k,v in CANDS.items():
    sc=BESTS[k]
    out=[]
    for lbl,sl in (('H1',slice(0,mid)),('H2',slice(mid,None))):
        b1=book_stats(BOOK.values[sl]); b2=book_stats((BOOK+v*sc).values[sl])
        out.append('%s %.2f->%.2f (%+.2f)'%(lbl,b1['sharpe'],b2['sharpe'],b2['sharpe']-b1['sharpe']))
    print('    %-13s scale %.2f   '%(k,sc)+'   '.join(out))

print('\n  DRAWDOWN DETAIL at optimal scale:')
for k,v in CANDS.items():
    sc=BESTS[k]; x=(BOOK+v*sc).values
    cu=np.cumsum(x); pk=np.maximum.accumulate(cu); dd=cu-pk
    print('    %-13s scale %.2f  maxDD $%.0f (base $%.0f)  time-underwater %.0f%% (base %.0f%%)'%(
        k,sc,dd.min(),b0['maxdd'],100*(dd<0).mean(),
        100*(np.cumsum(BOOK.values)-np.maximum.accumulate(np.cumsum(BOOK.values))<0).mean()))

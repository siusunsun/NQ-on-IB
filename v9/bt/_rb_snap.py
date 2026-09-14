# -*- coding: utf-8 -*-
"""_rb_snap.py  SNAPBACK full-history robustness audit. READ-ONLY. scratch only."""
import glob,sys,warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8',errors='replace'); sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _rb_lib import sharpe, dsr, book_stats, wf_folds
RNG=np.random.default_rng(11)
NQ_DIR='/root/tlbrc_paper/ref/data_full/NQ/'
PT=2.0   # 1 MNQ

def load_1m():
    parts=[]
    for f in sorted(glob.glob(NQ_DIR+'*.csv')):
        try: parts.append(pd.read_csv(f,usecols=['time','close']))
        except: pass
    d=pd.concat(parts,ignore_index=True)
    t=pd.to_datetime(d['time'],utc=True,errors='coerce')
    if t.isna().mean()>0.5: t=pd.to_datetime(d['time'],utc=True,unit='s',errors='coerce')
    d['time']=t
    d=d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')
    return d['close']

def daily_at(c1m,hh,mm):
    cutoff=c1m.index.normalize()+pd.Timedelta(hours=hh,minutes=mm)
    ok=c1m[c1m.index<=cutoff]
    day=ok.groupby(ok.index.normalize()).last(); day.index=day.index.tz_localize(None)
    return day.dropna()

def snap(close,lb,thr,hold=1,cost_frac=0.00015):
    z=(close-close.rolling(lb).mean())/close.rolling(lb).std(ddof=0)
    df=pd.DataFrame({'c':close,'z':z}).dropna()
    ret=df['c'].pct_change().fillna(0).values
    sig=(df['z'].values<-thr).astype(float)
    if hold>1:
        sig=pd.Series(sig).rolling(hold).max().fillna(0).values
    expo=np.r_[0.0,sig[:-1]]
    dpos=np.abs(np.diff(np.r_[0.0,expo]))
    r=expo*ret-(cost_frac/2)*dpos
    return pd.Series(r,index=df.index),pd.Series(expo,index=df.index),pd.Series(ret,index=df.index),pd.Series(dpos,index=df.index),df['c']

def nent(e):
    v=e.values; return int(((v!=0)&(np.r_[0,v[:-1]]==0)).sum())

c1m=load_1m()
nq=daily_at(c1m,23,59)
print('NQ daily bars:',len(nq),nq.index[0].date(),'->',nq.index[-1].date(),'yrs=%.2f'%(len(nq)/252))
ERA1=nq.index[(nq.index>=pd.Timestamp('2021-01-01'))&(nq.index<pd.Timestamp('2024-01-01'))]
ERA2=nq.index[(nq.index>=pd.Timestamp('2024-01-01'))]
print('era1 days',len(ERA1),'era2 days',len(ERA2))

LBS=[5,10,15,20,25,30,40]; THRS=[1.0,1.25,1.5,1.75,2.0,2.5]
print('')
print('='*108)
print('1. PARAMETER SURFACE  Sharpe (full | era1 2021-23 | era2 2024-26)   hold=1, cost 1.5bp')
print('='*108)
print(('%-5s'%'lb')+''.join('%16s'%('thr='+str(t)) for t in THRS))
surf={}
for lb in LBS:
    row='%-5s'%lb
    for thr in THRS:
        r,e,rr,dp,px=snap(nq,lb,thr)
        m1=r.index.isin(ERA1); m2=r.index.isin(ERA2)
        sf,s1,s2=sharpe(r.values),sharpe(r[m1].values),sharpe(r[m2].values)
        surf[(lb,thr)]=(sf,s1,s2,nent(e))
        row+='%6.2f%5.2f%5.2f'%(sf,s1,s2)
    print(row)
allsh=np.array([v[0] for v in surf.values()])
print('')
print('  surface: %d cells, Sharpe mean %.2f std %.2f, min %.2f max %.2f, cells>0: %d/%d'%(
      len(surf),allsh.mean(),allsh.std(ddof=1),allsh.min(),allsh.max(),(allsh>0).sum(),len(allsh)))
good=[(k,v) for k,v in surf.items() if v[1]>0.4 and v[2]>0.4]
print('  cells positive-in-BOTH-eras (era1>0.4 and era2>0.4): %d/%d'%(len(good),len(surf)))
for k,v in sorted(good,key=lambda x:-min(x[1][1],x[1][2]))[:12]:
    print('    lb=%-3d thr=%-5s full %.2f  era1 %.2f  era2 %.2f  entries %d'%(k[0],k[1],v[0],v[1],v[2],v[3]))
rk=sorted(surf.items(),key=lambda x:-min(x[1][1],x[1][2]))
print('  best by MIN(era1,era2) [refit-resistant]: '+', '.join('lb%s/thr%s(%.2f)'%(k[0],k[1],min(v[1],v[2])) for k,v in rk[:5]))
print('  deployed lb=20/thr=1.5 rank by min-era: %d of %d'%([i for i,(k,v) in enumerate(rk) if k==(20,1.5)][0]+1,len(rk)))
nb=[(lb,thr) for lb in [15,20,25] for thr in [1.25,1.5,1.75]]
print('  3x3 neighbourhood of deployed, full: '+' '.join('%.2f'%surf[k][0] for k in nb)+
      '  | mean %.2f min %.2f'%(np.mean([surf[k][0] for k in nb]),np.min([surf[k][0] for k in nb])))
print('  3x3 neighbourhood era1:              '+' '.join('%.2f'%surf[k][1] for k in nb))
print('  3x3 neighbourhood era2:              '+' '.join('%.2f'%surf[k][2] for k in nb))
line='  HOLD sensitivity (lb=20,thr=1.5): '
for h in [1,2,3,5]:
    r,e,rr,dp,px=snap(nq,20,1.5,hold=h); line+='hold%d %.2f  '%(h,sharpe(r.values))
print(line)

r,e,rr,dp,px=snap(nq,20,1.5)
print('')
print('='*108); print('2. PERMUTATION NULL (deployed lb=20 thr=1.5)'); print('='*108)
cost=r.values-e.values*rr.values; real=sharpe(r.values); ev=e.values; rv=rr.values
null=np.array([sharpe(ev*RNG.permutation(rv)+cost) for _ in range(500)])
print('  500 shuffled-return draws: real Sharpe %.3f, null mean %.3f std %.3f -> %.1fth pctile (p=%.4f)'%(
      real,null.mean(),null.std(),(null<real).mean()*100,1-(null<real).mean()))
null2=[]
for _ in range(500):
    sh=RNG.permutation(ev); dp2=np.abs(np.diff(np.r_[0.0,sh]))
    null2.append(sharpe(sh*rv-(0.00015/2)*dp2))
null2=np.array(null2)
print('  500 signal-shuffle draws (same #long days, random dates): %.1fth pctile, null mean %.3f'%(
      (null2<real).mean()*100,null2.mean()))

print('')
print('='*108); print('3. COST SENSITIVITY'); print('='*108)
for mult in [1,2,3]:
    r2,e2,_,_,_=snap(nq,20,1.5,cost_frac=0.00015*mult)
    print('  %dx (=%.1fbp r/t): Sharpe %.2f  net cum-ret %+.1f%%  era1 %.2f era2 %.2f'%(
        mult,1.5*mult,sharpe(r2.values),r2.values.sum()*100,
        sharpe(r2[r2.index.isin(ERA1)].values),sharpe(r2[r2.index.isin(ERA2)].values)))
print('  in $ at 1 MNQ (pt-change x $2), fixed pts/side cost:')
pxv=px.values; dpts=np.r_[0.0,np.diff(pxv)]
for cpts in [0.5,1.0,2.0,3.0]:
    pnl=e.values*dpts*PT - cpts*PT*dp.values
    st=book_stats(pnl)
    print('    {:.1f}pt/side: net ${:,.0f}  Sharpe {:.2f}  maxDD ${:,.0f}  ret/DD {:.2f}'.format(
        cpts,st['net'],st['sharpe'],st['maxdd'],st['retdd']))

print('')
print('='*108); print('4. DEFLATED SHARPE'); print('='*108)
NT=len(surf)*4*3
srstd=allsh.std(ddof=1)/np.sqrt(252)
sr,sr0,p=dsr(r.values, srstd, NT)
print('  N_trials = %d  (42 lb/thr cells x 4 hold settings x 3 daily anchors)'%NT)
print('  trial-Sharpe std (annualised) = %.3f'%allsh.std(ddof=1))
print('  SR daily %.4f (= %.2f ann);  SR0 expected-max-under-null = %.2f ann'%(sr,sr*np.sqrt(252),sr0*np.sqrt(252)))
print('  DSR = %.3f  -> %s at 0.95'%(p,'PASS' if p>=0.95 else 'FAIL'))
for NT2 in [50,126,504,2000]:
    _,s0b,pb=dsr(r.values,srstd,NT2); print('    sensitivity N_trials=%-5d SR0=%.2f ann  DSR=%.3f'%(NT2,s0b*np.sqrt(252),pb))

print('')
print('='*108); print('5. ERA STABILITY'); print('='*108)
print('  per-calendar-year (deployed params, no refit):')
for y in sorted(set(nq.index.year)):
    ry=r[r.index.year==y]; ey=e[e.index.year==y]
    if len(ry)<10: continue
    pxy=px[px.index.year==y]
    pnl=ey.values*np.r_[0.0,np.diff(pxy.values)]*PT
    print('    {}: Sharpe {:+.2f}  netret {:+5.1f}%  entries {:3d}  TiM {:2.0f}%  $@1MNQ {:+,.0f}'.format(
        y,sharpe(ry.values),ry.values.sum()*100,nent(ey),ey.mean()*100,pnl.sum()))
idx=pd.DatetimeIndex(r.index); vals=r.values
edges=np.linspace(0,len(vals),7).astype(int)
print('  walk-forward, 6 contiguous folds, deployed params FIXED (no refit):')
for i in range(6):
    a,b=edges[i],edges[i+1]
    print('    fold%d %s..%s n=%d  Sharpe %+.2f  netret %+.1f%%'%(i+1,idx[a].date(),idx[b-1].date(),b-a,sharpe(vals[a:b]),vals[a:b].sum()*100))
print('  anchored-expanding WF WITH refit (choose best lb/thr on all past folds, trade next):')
cache={}
for lb in LBS:
    for thr in THRS:
        rr2,_,_,_,_=snap(nq,lb,thr); cache[(lb,thr)]=rr2.reindex(idx).fillna(0).values
oos=[]
for i in range(1,6):
    a,b=edges[i],edges[i+1]
    best=max(cache.items(),key=lambda kv: sharpe(kv[1][:a]))
    o=sharpe(best[1][a:b]); oos.append(best[1][a:b])
    print('    fold%d refit->lb%s/thr%s (IS %.2f)  OOS %+.2f   [deployed same fold %+.2f]'%(
        i+1,best[0][0],best[0][1],sharpe(best[1][:a]),o,sharpe(vals[a:b])))
oo=np.concatenate(oos)
print('    stitched refit-OOS Sharpe %+.2f  vs deployed-fixed over same span %+.2f'%(sharpe(oo),sharpe(vals[edges[1]:])))

out=pd.DataFrame({'date':px.index,'pnl_usd':e.values*np.r_[0.0,np.diff(px.values)]*PT - 1.0*PT*dp.values})
out.to_csv('/root/v9/bt/_rb_snap_daily.csv',index=False)
print('')
print('  wrote _rb_snap_daily.csv (1 MNQ notional, 1.0pt/side cost) net $%.0f'%out.pnl_usd.sum())

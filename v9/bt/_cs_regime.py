"""Causal daily trend/chop regime features. All values for day D use data through D-1 close."""
import sys; sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _cs_lib import etmin, etdate, op, hi, lo, cl, N

def daily():
    m=(etmin>=9*60+30)&(etmin<16*60)
    d=etdate[m]
    df=pd.DataFrame({'d':d,'o':op[m],'h':hi[m],'l':lo[m],'c':cl[m]})
    g=df.groupby('d')
    D=pd.DataFrame({'o':g['o'].first(),'h':g['h'].max(),'l':g['l'].min(),'c':g['c'].last()})
    return D

def er(c,n):
    return (c.diff(n).abs()/c.diff().abs().rolling(n).sum())

def adx(D,n=14):
    up=D['h'].diff(); dn=-D['l'].diff()
    pdm=np.where((up>dn)&(up>0),up,0.0); ndm=np.where((dn>up)&(dn>0),dn,0.0)
    pc=D['c'].shift(1)
    tr=pd.concat([D['h']-D['l'],(D['h']-pc).abs(),(D['l']-pc).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    pdi=100*pd.Series(pdm,index=D.index).ewm(alpha=1/n,adjust=False,min_periods=n).mean()/atr
    ndi=100*pd.Series(ndm,index=D.index).ewm(alpha=1/n,adjust=False,min_periods=n).mean()/atr
    dx=100*(pdi-ndi).abs()/(pdi+ndi)
    return dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def build():
    D=daily()
    F=pd.DataFrame(index=D.index)
    F['er10']=er(D['c'],10); F['er20']=er(D['c'],20); F['er5']=er(D['c'],5)
    F['adx14']=adx(D,14)
    # % of last 20 days closing outside the prior 20-day close range
    hi20=D['c'].shift(1).rolling(20).max(); lo20=D['c'].shift(1).rolling(20).min()
    out=((D['c']>hi20)|(D['c']<lo20)).astype(float)
    F['brk20']=out.rolling(20).mean()
    # realized vol / range ratio: sum|dc| over 20d vs 20d high-low range (inverse of ER-ish)
    F['rng20']=(D['h'].rolling(20).max()-D['l'].rolling(20).min())/D['c'].shift(1).rolling(20).sum()*20
    F=F.shift(1)          # <-- CAUSAL: day D sees only through D-1
    # expanding median threshold, also causal (uses only prior values)
    for c in ['er5','er10','er20','adx14','brk20']:
        F[c+'_med']=F[c].expanding(min_periods=250).median().shift(1)
        F[c+'_hi']=(F[c]>=F[c+'_med'])
    return F

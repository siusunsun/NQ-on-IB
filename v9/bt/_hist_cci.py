"""_cci_bt.py - kristov18 Single-Trend CCI sleeve, standalone validation on our NQ 1-min.
Read-only research. Outputs _cci_trades.csv (AM+PM), prints yearly stats. NQ=$20/pt for
his-number validation; portfolio work uses points (scale-invariant)."""
import sys, glob
import numpy as np, pandas as pd, pytz
from collections import defaultdict
ET = pytz.timezone("America/New_York")
NQ_DIR = "/root/tlbrc_paper/ref/data_full/NQ"
PT_USD = 20.0   # 1 NQ for validation vs his headline

def load_all():
    fs = sorted(glob.glob(NQ_DIR+"/*.csv"))
    dfs=[]
    for f in fs:
        d=pd.read_csv(f); d.columns=[c.strip().lower() for c in d.columns]
        tc=next(c for c in ('time','timestamp','date','datetime') if c in d.columns)
        d[tc]=pd.to_datetime(d[tc],utc=True,errors='coerce')
        d=d.dropna(subset=[tc]).set_index(tc)
        if 'volume' not in d.columns: d['volume']=0.0
        dfs.append(d[['open','high','low','close','volume']].astype(float))
    df=pd.concat(dfs).sort_index()
    df=df[~df.index.duplicated(keep='last')].sort_index()
    return df

def wilder_atr(h,l,c,n=14):
    pc=c.shift(1)
    tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

def cci(h,l,c,n=20):
    tp=(h+l+c)/3.0
    sma=tp.rolling(n).mean()
    md=tp.rolling(n).apply(lambda x: np.abs(x-x.mean()).mean(), raw=True)
    return (tp-sma)/(0.015*md)

def main():
    df=load_all()
    idx_et=df.index.tz_convert(ET)
    df=df.copy()
    et=idx_et
    sess=np.where(et.hour>=18, et.normalize(), (et-pd.Timedelta(days=1)).normalize())
    df['vsess']=pd.to_datetime(sess)
    tp=(df['high']+df['low']+df['close'])/3.0
    cum_pv=(tp*df['volume']).groupby(df['vsess']).cumsum()
    cum_v=df['volume'].groupby(df['vsess']).cumsum().replace(0,np.nan)
    df['vwap']=(cum_pv/cum_v).ffill()
    df['cci']=cci(df['high'],df['low'],df['close'],20)
    o5=df['open'].resample('5min',label='left',closed='left').first()
    h5=df['high'].resample('5min',label='left',closed='left').max()
    l5=df['low'].resample('5min',label='left',closed='left').min()
    c5=df['close'].resample('5min',label='left',closed='left').last()
    b5=pd.DataFrame({'o':o5,'h':h5,'l':l5,'c':c5}).dropna()
    ema50=b5['c'].ewm(span=50,adjust=False,min_periods=50).mean()
    atr5=wilder_atr(b5['h'],b5['l'],b5['c'],14)
    ema50_30ago=ema50.shift(6)
    low20_5m=b5['l'].rolling(20).min()
    high20_5m=b5['h'].rolling(20).max()
    b5et=b5.index.tz_convert(ET)
    slot=b5et.hour*60+b5et.minute
    tmp=pd.DataFrame({'atr':atr5.values,'slot':slot},index=b5.index)
    norm=pd.Series(index=b5.index,dtype=float)
    for s,g in tmp.groupby('slot'):
        m=g['atr'].shift(1).rolling(20,min_periods=10).median()
        norm.loc[g.index]=m.values
    avail=b5.index + pd.Timedelta(minutes=5)
    F=pd.DataFrame({'ema50':ema50.values,'atr5':atr5.values,'ema50_30ago':ema50_30ago.values,
                    'low20':low20_5m.values,'high20':high20_5m.values,'norm':norm.values},
                   index=avail)
    F=F[~F.index.duplicated(keep='last')]
    F5=F.reindex(df.index,method='ffill')
    for cc in F.columns: df[cc]=F5[cc].values
    idx_et2=df.index.tz_convert(ET)
    df['etmin']=idx_et2.hour*60+idx_et2.minute
    etdate_arr=np.array(idx_et2.date)
    px=df['close'].values; op=df['open'].values; hi=df['high'].values; lo=df['low'].values
    cciv=df['cci'].values; ema=df['ema50'].values; vw=df['vwap'].values
    atrv=df['atr5'].values; e30=df['ema50_30ago'].values
    l20=df['low20'].values; h20=df['high20'].values; nrm=df['norm'].values
    etmin=df['etmin'].values; n=len(df); ts=df.index

    def scan(day_rows, w0, w1, kstop):
        for ii in range(1,len(day_rows)):
            i=day_rows[ii]
            m=etmin[i]
            if m< w0 or m>=w1: continue
            pc=cciv[i-1]; cc=cciv[i]
            if np.isnan(pc) or np.isnan(cc): continue
            if np.isnan(ema[i]) or np.isnan(vw[i]) or np.isnan(atrv[i]) or np.isnan(e30[i]) or np.isnan(nrm[i]): continue
            price=px[i]
            if not (atrv[i] <= 1.0*nrm[i]): continue
            long_cross = pc< -100 and cc>=-100
            short_cross= pc> 100 and cc<=100
            if long_cross and px[i]>ema[i] and (ema[i]-vw[i])>=0.0015*price and ema[i]>e30[i]:
                if i+1>=n or etdate_arr[i+1]!=etdate_arr[i]: continue
                return ('long',i+1,op[i+1],l20[i]-kstop*atrv[i])
            if short_cross and px[i]<ema[i] and (vw[i]-ema[i])>=0.0015*price and ema[i]<e30[i]:
                if i+1>=n or etdate_arr[i+1]!=etdate_arr[i]: continue
                return ('short',i+1,op[i+1],h20[i]+kstop*atrv[i])
        return None

    def simulate(direction,fi,entry,stop):
        j=fi
        while j<n and etdate_arr[j]==etdate_arr[fi]:
            if etmin[j]>=16*60:
                return (ts[j],op[j],'EOD')
            if direction=='long' and lo[j]<=stop: return (ts[j],stop,'STOP')
            if direction=='short' and hi[j]>=stop: return (ts[j],stop,'STOP')
            j+=1
        jj=min(j,n-1); return (ts[jj],px[jj],'EOD')

    rth_mask=(etmin>=9*60+30)&(etmin<16*60)
    positions=np.arange(n)[rth_mask]; dkeys=etdate_arr[rth_mask]
    daymap=defaultdict(list)
    for pos,dk in zip(positions,dkeys): daymap[dk].append(pos)

    trades=[]
    for dk in sorted(daymap.keys()):
        rows=daymap[dk]
        for (w0,w1,kstop,slotname) in [(9*60+30,12*60,1.0,'AM'),(13*60,15*60,1.5,'PM')]:
            r=scan(rows,w0,w1,kstop)
            if not r: continue
            direction,fi,entry,stop=r
            xt,xp,rs=simulate(direction,fi,entry,stop)
            sgn=1 if direction=='long' else -1
            pts=sgn*(xp-entry)
            trades.append(dict(slot=slotname,date=str(dk),direction=direction,
                entry_time=str(ts[fi].tz_convert(ET)),entry=round(entry,2),stop=round(stop,2),
                exit_time=str(xt.tz_convert(ET)),exit=round(xp,2),reason=rs,
                pts=round(pts,2),risk=round(abs(entry-stop),2)))
    T=pd.DataFrame(trades)
    T.to_csv('/root/v9/bt/_hist_cci_trades.csv',index=False)
    print("TOTAL trades rows:",len(T),"slots:",T['slot'].value_counts().to_dict() if len(T) else {})
    def stats(sub,label):
        if len(sub)==0: print(label,"no trades"); return
        pts=sub['pts']; usd=pts*PT_USD
        print(f"\n=== {label} ===  n={len(pts)}")
        print(f"  net_pts={pts.sum():.1f} net_usd=${usd.sum():,.0f} win%={(pts>0).mean()*100:.1f} avg_pts={pts.mean():.2f}")
        sub2=sub.copy(); sub2['yr']=sub2['date'].str[:4]
        for yr,g in sub2.groupby('yr'):
            gp=g['pts']
            print(f"    {yr}: n={len(gp):3d} net=${gp.sum()*PT_USD:>9,.0f} win%={(gp>0).mean()*100:4.1f}")
    stats(T[T['slot']=='AM'],"SINGLE-SLOT (AM only, k=1.0)")
    T['w']=np.where(T['slot']=='AM',1.0,0.33)
    T['wpts']=T['pts']*T['w']
    print(f"\n=== TWO-SLOT (AM 1.0 + PM 0.33) ===  n={len(T)}  net_usd=${(T['wpts']*PT_USD).sum():,.0f}")
    tt=T.copy(); tt['yr']=tt['date'].str[:4]
    for yr,g in tt.groupby('yr'):
        print(f"    {yr}: net=${(g['wpts']*PT_USD).sum():>9,.0f}  trades={len(g)} (AM {(g['slot']=='AM').sum()} / PM {(g['slot']=='PM').sum()})")
main()

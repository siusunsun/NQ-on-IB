"""_cs_stageA.py - load full-history NQ 1-min ONCE, build deployed-spec features,
cache compact float32 arrays to _cs_feat.npz. READ-ONLY research."""
import glob, gc, os, resource
import numpy as np, pandas as pd, pytz
from numpy.lib.stride_tricks import sliding_window_view
ET = pytz.timezone("America/New_York")
DIR = "/root/tlbrc_paper/ref/data_full/NQ"
OUT = "/root/v9/bt/_cs_feat.npz"

def rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.0

def load_all():
    dfs=[]
    for f in sorted(glob.glob(DIR+"/*.csv")):
        d=pd.read_csv(f)
        d.columns=[c.strip().lower() for c in d.columns]
        tc=next(c for c in ('time','timestamp','date','datetime') if c in d.columns)
        d[tc]=pd.to_datetime(d[tc],utc=True,errors='coerce')
        d=d.dropna(subset=[tc]).set_index(tc)
        if 'volume' not in d.columns: d['volume']=0.0
        dfs.append(d[['open','high','low','close','volume']].astype(np.float32))
        del d
    df=pd.concat(dfs).sort_index()
    del dfs; gc.collect()
    df=df[~df.index.duplicated(keep='last')].sort_index()
    return df

def mad_roll(v,n=20,step=200000):
    out=np.full(len(v),np.nan)
    for s in range(0,len(v),step):
        a=max(0,s-(n-1)); b=min(len(v),s+step)
        seg=v[a:b]
        if len(seg)<n: continue
        w=sliding_window_view(seg,n)
        m=w.mean(axis=1)
        md=np.abs(w-m[:,None]).mean(axis=1)
        ends=a+np.arange(len(w))+n-1
        sel=(ends>=s)&(ends<b)
        out[ends[sel]]=md[sel]
        del w,m,md
    return out

def wilder_atr(h,l,c,n=14):
    pc=c.shift(1)
    tr=pd.concat([(h-l),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean()

df=load_all()
print("bars",len(df),"from",df.index[0],"to",df.index[-1],"RSSMB %.0f"%rss(),flush=True)
et=df.index.tz_convert(ET)
# sanity: volume by ET hour
vh=pd.Series(df['volume'].values.astype('float64'),index=et.hour).groupby(level=0).sum()
print("vol by ET hour (top6):",vh.sort_values(ascending=False).head(6).round(0).to_dict(),flush=True)

sess=np.where(et.hour>=18, et.normalize(), (et-pd.Timedelta(days=1)).normalize())
vsess=pd.to_datetime(sess)
h64=df['high'].astype('float64'); l64=df['low'].astype('float64'); c64=df['close'].astype('float64')
tp=(h64+l64+c64)/3.0
cum_pv=(tp*df['volume'].astype('float64')).groupby(vsess).cumsum()
cum_v=df['volume'].astype('float64').groupby(vsess).cumsum().replace(0,np.nan)
vwap=(cum_pv/cum_v).ffill().values
del cum_pv,cum_v,sess,vsess; gc.collect()
sma=tp.rolling(20).mean().values
md=mad_roll(tp.values,20)
cciv=(tp.values-sma)/(0.015*md)
del sma,md,tp; gc.collect()
print("cci done RSSMB %.0f"%rss(),flush=True)

o5=df['open'].astype('float64').resample('5min',label='left',closed='left').first()
h5=df['high'].astype('float64').resample('5min',label='left',closed='left').max()
l5=df['low'].astype('float64').resample('5min',label='left',closed='left').min()
c5=df['close'].astype('float64').resample('5min',label='left',closed='left').last()
b5=pd.DataFrame({'o':o5,'h':h5,'l':l5,'c':c5}).dropna()
del o5,h5,l5,c5
ema50=b5['c'].ewm(span=50,adjust=False,min_periods=50).mean()
atr5=wilder_atr(b5['h'],b5['l'],b5['c'],14)
ema50_30ago=ema50.shift(6)
low20=b5['l'].rolling(20).min(); high20=b5['h'].rolling(20).max()
b5et=b5.index.tz_convert(ET)
slot=b5et.hour*60+b5et.minute
tmp=pd.DataFrame({'atr':atr5.values,'slot':slot},index=b5.index)
norm=pd.Series(index=b5.index,dtype='float64')
for s,g in tmp.groupby('slot'):
    norm.loc[g.index]=g['atr'].shift(1).rolling(20,min_periods=10).median().values
avail=b5.index+pd.Timedelta(minutes=5)
F=pd.DataFrame({'ema50':ema50.values,'atr5':atr5.values,'ema50_30ago':ema50_30ago.values,
                'low20':low20.values,'high20':high20.values,'norm':norm.values},index=avail)
F=F[~F.index.duplicated(keep='last')]
F5=F.reindex(df.index,method='ffill')
del F,tmp,norm,ema50,atr5,ema50_30ago,low20,high20,b5,avail; gc.collect()
print("5m feats done RSSMB %.0f"%rss(),flush=True)

etmin=(et.hour*60+et.minute).values.astype(np.int16)
etdate=np.array([d.toordinal() for d in et.date],dtype=np.int32)
np.savez_compressed(OUT,
    ts=df.index.values.astype('datetime64[s]').astype(np.int64),
    op=df['open'].values, hi=df['high'].values, lo=df['low'].values, cl=df['close'].values,
    cci=cciv.astype(np.float32), vwap=vwap.astype(np.float32),
    ema50=F5['ema50'].values.astype(np.float32), atr5=F5['atr5'].values.astype(np.float32),
    e30=F5['ema50_30ago'].values.astype(np.float32),
    low20=F5['low20'].values.astype(np.float32), high20=F5['high20'].values.astype(np.float32),
    norm=F5['norm'].values.astype(np.float32),
    etmin=etmin, etdate=etdate)
print("saved",OUT,os.path.getsize(OUT)/1e6,"MB  peakRSSMB %.0f"%rss(),flush=True)

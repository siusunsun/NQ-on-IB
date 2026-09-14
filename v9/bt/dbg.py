from pathlib import Path
import numpy as np, pandas as pd, pytz
ET=pytz.timezone("America/New_York"); NQ_DIR=Path("/root/v9/data_1m/NQ")
def load_csv(p):
    df=pd.read_csv(p); df.columns=[c.strip().lower() for c in df.columns]
    tc=next(c for c in ("time","timestamp","date","datetime") if c in df.columns)
    df[tc]=pd.to_datetime(df[tc],utc=True,errors="coerce")
    df=df.dropna(subset=[tc]).set_index(tc).sort_index()
    if "volume" not in df.columns: df["volume"]=0.0
    return df[["open","high","low","close","volume"]].astype(float)
df=pd.concat([load_csv(f) for f in sorted(NQ_DIR.glob("*.csv"))]).sort_index()
df=df[~df.index.duplicated(keep="last")].sort_index()
etidx=df.index.tz_convert(ET); df=df.assign(etmin=etidx.hour*60+etidx.minute,etdate=etidx.date)
rth=df[(df.etmin>=570)&(df.etmin<960)]
# debug one 2024 day and one 2026 day
for target in ["2024-03-15","2026-07-15"]:
    g=rth[rth.etdate==pd.Timestamp(target).date()]
    orb=g[(g.etmin>=570)&(g.etmin<600)]; post=g[g.etmin>=600]
    orh=orb.high.max(); orl=orb.low.min()
    print(target,"orh",orh,"orl",orl,"dayclose",post.close.iloc[-1],"post_high",post.high.max(),"post_low",post.low.min(),"n_orb",len(orb),"n_post",len(post))
# VWAP debug: max |z| distribution
d5=df.copy(); d5["u5"]=(d5.index.view('int64')//(5*60*10**9))
b5=d5.groupby("u5").agg(h=("high","max"),l=("low","min"),c=("close","last"),v=("volume","sum"))
b5["utc"]=pd.to_datetime(b5.index*5*60,unit="s",utc=True); b5["sess"]=b5.utc.dt.date; b5["tp"]=(b5.h+b5.l+b5.c)/3
mx=[]
for sess,g in list(b5.groupby("sess"))[300:320]:
    g=g.sort_values("utc"); cv=cvp=cvsq=0.0; zz=[]
    for _,r in g.iterrows():
        vol=max(r.v,1e-9); cv+=vol;cvp+=vol*r.tp;cvsq+=vol*r.tp*r.tp
        m=cvp/cv; sd=np.sqrt(max(cvsq/cv-m*m,0))
        zz.append((r.c-m)/sd if sd>0 else np.nan)
    zz=np.array(zz,dtype=float); print(sess,"nbars",len(zz),"maxabsz",np.nanmax(np.abs(zz)) if len(zz) else None)

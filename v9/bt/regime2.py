import sys, importlib.util
from pathlib import Path
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
NQ_DIR = Path("/root/v9/data_1m/NQ")
def load_csv(p):
    df=pd.read_csv(p); df.columns=[c.strip().lower() for c in df.columns]
    tc=next(c for c in ("time","timestamp","date","datetime") if c in df.columns)
    df[tc]=pd.to_datetime(df[tc],utc=True,errors="coerce")
    df=df.dropna(subset=[tc]).set_index(tc).sort_index()
    if "volume" not in df.columns: df["volume"]=0.0
    return df[["open","high","low","close","volume"]].astype(float)
def load_all():
    df=pd.concat([load_csv(f) for f in sorted(NQ_DIR.glob("*.csv"))]).sort_index()
    if df.index.tz is None: df=df.tz_localize("UTC")
    return df[~df.index.duplicated(keep="last")].sort_index()
df=load_all()
etidx=df.index.tz_convert(ET)
df=df.assign(et=etidx,etdate=etidx.date,etmin=etidx.hour*60+etidx.minute)
BASE=(pd.Timestamp("2024-01-01").date(),pd.Timestamp("2026-05-31").date())
REC=(pd.Timestamp("2026-06-01").date(),pd.Timestamp("2026-08-06").date())
def win(d):
    if BASE[0]<=d<=BASE[1]: return "BASE"
    if REC[0]<=d<=REC[1]: return "REC"
    return None

rth=df[(df.etmin>=570)&(df.etmin<960)]
# ---- ORB v2 ----
rows=[]
for d,g in rth.groupby("etdate"):
    orb=g[(g.etmin>=570)&(g.etmin<600)]; post=g[g.etmin>=600]
    if len(orb)<10 or len(post)<20: continue
    orh=orb.high.max(); orl=orb.low.min(); w=orh-orl
    if w<=0: continue
    brk=None
    for t,r in post.iterrows():
        if r.high>orh: brk=("up",t); break
        if r.low<orl: brk=("dn",t); break
    if brk is None:
        rows.append((d,"none",np.nan,np.nan,w)); continue
    dirn,bt=brk; after=post[post.index>=bt]
    dayclose=post.close.iloc[-1]
    if dirn=="up":
        held = dayclose>orh
        full_rev = (after.low.min()<orl)      # came all the way back to other side
        mfe=(after.high.max()-orh)
    else:
        held = dayclose<orl
        full_rev = (after.high.max()>orh)
        mfe=(orl-after.low.min())
    rows.append((d,dirn,held,full_rev,mfe/w))
orbdf=pd.DataFrame(rows,columns=["etdate","dir","held","fullrev","mfe_w"])
orbdf["win"]=[win(d) for d in orbdf.etdate]
orbdf["ym"]=[pd.Timestamp(d).strftime("%Y-%m") for d in orbdf.etdate]

print("########## ORB regime ##########")
for tag in ["BASE","REC"]:
    o=orbdf[(orbdf.win==tag)&(orbdf.dir.isin(['up','dn']))]
    print(f"[{tag}] brk_days={len(o)} held(close beyond)={100*o.held.mean():.1f}%  full-reversal(trap)={100*o.fullrev.mean():.1f}%  medianMFE/ORwidth={o.mfe_w.median():.2f}")
print("\n-- ORB by month 2026 --")
for ym,o in orbdf[orbdf.dir.isin(['up','dn'])].groupby("ym"):
    if ym<"2026-01": continue
    print(f"  {ym}: n={len(o):2d} held={100*o.held.mean():4.0f}% fullrev={100*o.fullrev.mean():4.0f}% MFE/w={o.mfe_w.median():.2f}")

# ---- VWAP v2 proper episode detection (5min, 00:00 UTC anchor) ----
d5=df.copy(); d5["u5"]=(d5.index.view('int64')//(5*60*10**9))
b5=d5.groupby("u5").agg(t=("et","last"),h=("high","max"),l=("low","min"),c=("close","last"),v=("volume","sum"))
b5["utc"]=pd.to_datetime(b5.index*5*60,unit="s",utc=True)
b5["sess"]=b5.utc.dt.date; b5["tp"]=(b5.h+b5.l+b5.c)/3
ev=[]
for sess,g in b5.groupby("sess"):
    g=g.sort_values("utc"); cv=cvp=cvsq=0.0; vw=[];sd=[]
    for _,r in g.iterrows():
        vol=max(r.v,1e-9); cv+=vol;cvp+=vol*r.tp;cvsq+=vol*r.tp*r.tp
        m=cvp/cv; vw.append(m); sd.append(np.sqrt(max(cvsq/cv-m*m,0)))
    g=g.assign(vwap=vw,sd=sd); g["z"]=(g.c-g.vwap)/g.sd.replace(0,np.nan)
    z=g.z.values; sdv=g.sd.values; n=len(z)
    inep=False
    for i in range(n):
        if pd.isna(z[i]) or sdv[i]<=0: continue
        if not inep and abs(z[i])>=2:  # new episode
            inep=True; side=np.sign(z[i])
            out="neither"
            for j in range(i+1,min(i+13,n)):
                if pd.isna(z[j]): continue
                if abs(z[j])<1.0: out="revert"; break
                if np.sign(z[j])==side and abs(z[j])>=3.0: out="keepgoing"; break
            ev.append((sess,side,out))
        elif inep and abs(z[i])<1.0:
            inep=False
revdf=pd.DataFrame(ev,columns=["etdate","side","outcome"])
revdf["win"]=[win(d) for d in revdf.etdate]
print("\n########## VWAP 2sd reversion (5min,00:00UTC anchor) ##########")
for tag in ["BASE","REC"]:
    rv=revdf[revdf.win==tag]
    vc=rv.outcome.value_counts(normalize=True)*100
    print(f"[{tag}] events={len(rv)} revert={vc.get('revert',0):.1f}% keepgoing={vc.get('keepgoing',0):.1f}% neither={vc.get('neither',0):.1f}%")
    rvb=rv[rv.side<0]; vcb=rvb.outcome.value_counts(normalize=True)*100
    print(f"    below-VWAP(V9-long) n={len(rvb)} revert={vcb.get('revert',0):.1f}% keepgoing={vcb.get('keepgoing',0):.1f}%")

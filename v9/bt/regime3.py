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
BASE=(pd.Timestamp("2024-01-01").date(),pd.Timestamp("2026-05-31").date())
REC=(pd.Timestamp("2026-06-01").date(),pd.Timestamp("2026-08-06").date())
def win(d):
    if BASE[0]<=d<=BASE[1]: return "BASE"
    if REC[0]<=d<=REC[1]: return "REC"
    return None
rth=df[(df.etmin>=570)&(df.etmin<960)]
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
    if brk is None: rows.append([d,"none",np.nan,np.nan,np.nan,np.nan]); continue
    dirn,bt=brk; after=post[post.index>=bt]; dayclose=post.close.iloc[-1]
    # follow-through: reach +0.5w extension beyond boundary BEFORE returning to opposite boundary
    ft=np.nan; seq=after
    if dirn=="up":
        ext=orh+0.5*w; opp=orl; held=int(dayclose>orh)
        ftv=0
        for t,r in seq.iterrows():
            if r.high>=ext: ftv=1; break
            if r.low<opp: ftv=0; break
        mfe=(after.high.max()-orh)/w; fullrev=int(after.low.min()<orl)
    else:
        ext=orl-0.5*w; opp=orh; held=int(dayclose<orl)
        ftv=0
        for t,r in seq.iterrows():
            if r.low<=ext: ftv=1; break
            if r.high>opp: ftv=0; break
        mfe=(orl-after.low.min())/w; fullrev=int(after.high.max()>orh)
    rows.append([d,dirn,held,ftv,fullrev,mfe])
orbdf=pd.DataFrame(rows,columns=["etdate","dir","held","ft","fullrev","mfe_w"])
orbdf["win"]=[win(d) for d in orbdf.etdate]
orbdf["ym"]=[pd.Timestamp(d).strftime("%Y-%m") for d in orbdf.etdate]
brk=orbdf[orbdf.dir.isin(['up','dn'])].copy()
print("########## ORB regime ##########")
for tag in ["BASE","REC"]:
    o=brk[brk.win==tag]
    print(f"[{tag}] brk_days={len(o)} followthru(+0.5w before opp)={100*o.ft.mean():.1f}%  held@close={100*o.held.mean():.1f}%  fullrev={100*o.fullrev.mean():.1f}%  medMFE/w={o.mfe_w.median():.2f}")
print("\n-- ORB by month 2026 --")
for ym,o in brk.groupby("ym"):
    if ym<"2026-01": continue
    print(f"  {ym}: n={len(o):2d} followthru={100*o.ft.mean():4.0f}% held={100*o.held.mean():4.0f}% fullrev={100*o.fullrev.mean():4.0f}% MFE/w={o.mfe_w.median():.2f}")

# ---- VWAP v3 floor('5min') ----
b=df[["high","low","close","volume"]].copy()
b["t5"]=df.index.floor("5min")
g5=b.groupby("t5").agg(h=("high","max"),l=("low","min"),c=("close","last"),v=("volume","sum"))
g5["sess"]=g5.index.tz_convert("UTC").date; g5["tp"]=(g5.h+g5.l+g5.c)/3
ev=[]
for sess,g in g5.groupby("sess"):
    g=g.sort_index(); cv=cvp=cvsq=0.0; z=[]
    for tp,vol in zip(g.tp.values,g.v.values):
        vol=max(vol,1e-9); cv+=vol;cvp+=vol*tp;cvsq+=vol*tp*tp
        m=cvp/cv; sd=np.sqrt(max(cvsq/cv-m*m,0)); z.append((g.c.loc[g.index[len(z)]]-m)/sd if sd>0 else np.nan)
    z=np.array(z,dtype=float); n=len(z); inep=False
    for i in range(n):
        if np.isnan(z[i]): continue
        if not inep and abs(z[i])>=2:
            inep=True; side=np.sign(z[i]); out="neither"
            for j in range(i+1,min(i+13,n)):
                if np.isnan(z[j]): continue
                if abs(z[j])<1.0: out="revert"; break
                if np.sign(z[j])==side and abs(z[j])>=3.0: out="keepgoing"; break
            ev.append((sess,side,out))
        elif inep and abs(z[i])<1.0: inep=False
rev=pd.DataFrame(ev,columns=["etdate","side","outcome"]); rev["win"]=[win(d) for d in rev.etdate]
print("\n########## VWAP 2sd reversion (5min,00:00UTC anchor,k=2) ##########")
for tag in ["BASE","REC"]:
    r=rev[rev.win==tag]; vc=r.outcome.value_counts(normalize=True)*100
    print(f"[{tag}] events={len(r)} revert={vc.get('revert',0):.1f}% keepgoing={vc.get('keepgoing',0):.1f}% neither={vc.get('neither',0):.1f}%")
    rb=r[r.side<0]; vb=rb.outcome.value_counts(normalize=True)*100
    print(f"    below-VWAP(V9-long) n={len(rb)} revert={vb.get('revert',0):.1f}% keepgoing={vb.get('keepgoing',0):.1f}%")

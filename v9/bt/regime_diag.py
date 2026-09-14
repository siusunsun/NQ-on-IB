import sys, importlib.util
from pathlib import Path
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
NQ_DIR = Path("/root/v9/data_1m/NQ")

def load_csv(path):
    df = pd.read_csv(path)
    df.columns=[c.strip().lower() for c in df.columns]
    tcol=next(c for c in ("time","timestamp","date","datetime") if c in df.columns)
    df[tcol]=pd.to_datetime(df[tcol], utc=True, errors="coerce")
    df=df.dropna(subset=[tcol]).set_index(tcol).sort_index()
    if "volume" not in df.columns: df["volume"]=0.0
    return df[["open","high","low","close","volume"]].astype(float)

def load_all():
    files=sorted(NQ_DIR.glob("*.csv"))
    df=pd.concat([load_csv(f) for f in files]).sort_index(kind="mergesort")  # 2026-09-14: STABLE sort
    if df.index.tz is None: df=df.tz_localize("UTC")
    df=df[~df.index.duplicated(keep="last")].sort_index()
    return df

df=load_all()
print("COVERAGE:", df.index.min(), "->", df.index.max(), "rows=",len(df))
etidx=df.index.tz_convert(ET)
df=df.assign(et=etidx, etdate=etidx.date, ethour=etidx.hour, etmin=etidx.hour*60+etidx.minute)

BASE=(pd.Timestamp("2024-01-01").date(), pd.Timestamp("2026-05-31").date())
REC =(pd.Timestamp("2026-06-01").date(), pd.Timestamp("2026-08-06").date())
def win(d):
    if BASE[0]<=d<=BASE[1]: return "BASE"
    if REC[0]<=d<=REC[1]: return "REC"
    return None

# ---------- RTH daily bars ----------
rth=df[(df.etmin>=570)&(df.etmin<960)]   # 09:30-16:00
daily=rth.groupby("etdate").agg(o=("open","first"),h=("high","max"),l=("low","min"),c=("close","last"))
daily=daily.dropna()
daily["range"]=daily.h-daily.l
daily["rangepct"]=daily["range"]/daily.c*100
# True range with prev RTH close
daily["pc"]=daily.c.shift(1)
daily["tr"]=np.maximum(daily.h-daily.l, np.maximum((daily.h-daily.pc).abs(),(daily.l-daily.pc).abs()))
daily["atr14"]=daily.tr.rolling(14).mean()
daily["win"]=[win(d) for d in daily.index]

# ---------- Efficiency ratio on RTH 5-min closes ----------
rth5=rth.copy()
rth5["b5"]=(rth5.etmin//5)
fivemin=rth5.groupby(["etdate","b5"]).agg(c=("close","last"),o=("open","first")).reset_index()
er_rows=[]
for d,g in fivemin.groupby("etdate"):
    cl=g.c.values
    if len(cl)<5: continue
    net=abs(cl[-1]-g.o.values[0])
    path=np.abs(np.diff(cl)).sum()
    er=net/path if path>0 else np.nan
    er_rows.append((d,er))
erdf=pd.DataFrame(er_rows,columns=["etdate","er"]).set_index("etdate")
erdf["win"]=[win(d) for d in erdf.index]

# ---------- ORB: 09:30-10:00 opening range, first breakout after 10:00, continue vs trap ----------
orb_rows=[]
for d,g in rth.groupby("etdate"):
    orbar=g[(g.etmin>=570)&(g.etmin<600)]
    post=g[g.etmin>=600]
    if len(orbar)<10 or len(post)<20: continue
    orh=orbar.high.max(); orl=orbar.low.min(); w=orh-orl
    if w<=0: continue
    # walk post bars, find first breakout
    brk=None
    for t,r in post.iterrows():
        if r.high>orh: brk=("up",orh); bt=t; break
        if r.low<orl: brk=("dn",orl); bt=t; break
    if brk is None:
        orb_rows.append((d,"none",np.nan)); continue
    dirn,lvl=brk
    after=post[post.index>bt]
    tgt = orh+w if dirn=="up" else orl-w   # 1x extension
    worked=None
    for t,r in after.iterrows():
        if dirn=="up":
            if r.high>=tgt: worked=True; break
            if r.low<orh: worked=False; break   # back inside range = trap
        else:
            if r.low<=tgt: worked=True; break
            if r.high>orl: worked=False; break
    if worked is None: worked=False  # neither -> died flat, count as not-worked(no continuation)
    orb_rows.append((d,dirn,worked))
orbdf=pd.DataFrame(orb_rows,columns=["etdate","dir","worked"])
orbdf["win"]=[win(d) for d in orbdf.etdate]

# ---------- VWAP reversion: 5-min bars, 00:00 UTC session anchor, k=2 ----------
d5=df.copy()
d5["u5"]=(d5.index.view('int64')//(5*60*10**9))
b5=d5.groupby("u5").agg(t=("et","last"),o=("open","first"),h=("high","max"),l=("low","min"),c=("close","last"),v=("volume","sum"))
b5["utc"]=pd.to_datetime(b5.index*5*60,unit="s",utc=True)
b5["sess"]=b5.utc.dt.date   # 00:00 UTC anchor => session = UTC date
b5["tp"]=(b5.h+b5.l+b5.c)/3
rev_rows=[]
for sess,g in b5.groupby("sess"):
    g=g.sort_values("utc")
    cv=0.0;cvp=0.0;cvsq=0.0
    vw=[];sd=[]
    for _,r in g.iterrows():
        vol=max(r.v,1e-9)
        cv+=vol;cvp+=vol*r.tp;cvsq+=vol*r.tp*r.tp
        m=cvp/cv;var=cvsq/cv-m*m
        vw.append(m);sd.append(np.sqrt(max(var,0)))
    g=g.assign(vwap=vw,sd=sd)
    g["z"]=(g.c-g.vwap)/g.sd.replace(0,np.nan)
    arr=g.reset_index(drop=True)
    n=len(arr)
    i=0
    while i<n:
        z=arr.z.iloc[i]
        if pd.notna(z) and abs(z)>=2 and arr.sd.iloc[i]>0:
            side=np.sign(z)
            # look forward up to 12 bars (60min): revert(|z|<1) vs keepgoing(|z|>=3 further same side)
            outcome=None
            for j in range(i+1,min(i+13,n)):
                zj=arr.z.iloc[j]
                if pd.isna(zj): continue
                if abs(zj)<1.0: outcome="revert"; break
                if np.sign(zj)==side and abs(zj)>=3.0: outcome="keepgoing"; break
            if outcome is None: outcome="neither"
            d=arr.t.iloc[i].date()
            rev_rows.append((d,side,outcome))
            i=j if 'j' in dir() else i+1
            i+=3  # skip a few bars to avoid double-count of same stretch
        else:
            i+=1
revdf=pd.DataFrame(rev_rows,columns=["etdate","side","outcome"])
revdf["win"]=[win(d) for d in revdf.etdate]

# ================= REPORT =================
def rep(name):
    print("\n==================", name, "==================")
    for tag,dd,lbl in [("BASE",BASE,"2024-01..2026-05"),("REC",REC,"2026-06-01..2026-08-06")]:
        pass

print("\n########## PART 1 REGIME ##########")
for tag in ["BASE","REC"]:
    sub=daily[daily.win==tag].dropna(subset=["atr14"])
    subr=daily[daily.win==tag]
    print(f"\n[{tag}] n_days={len(subr)}")
    print(f"  ATR14 pts: mean={sub.atr14.mean():.1f} median={sub.atr14.median():.1f} last={sub.atr14.iloc[-1] if len(sub) else float('nan'):.1f}")
    print(f"  RTH range pts: mean={subr.range.mean():.1f} median={subr.range.median():.1f}")
    print(f"  RTH range %price: mean={subr.rangepct.mean():.3f}% median={subr.rangepct.median():.3f}%")
    e=erdf[erdf.win==tag].er.dropna()
    print(f"  ER mean={e.mean():.3f}  trendy(ER>0.4)={100*(e>0.4).mean():.1f}%  choppy(ER<0.2)={100*(e<0.2).mean():.1f}%  n={len(e)}")
    o=orbdf[(orbdf.win==tag)&(orbdf.dir.isin(['up','dn']))]
    if len(o):
        fail=100*(~o.worked.astype(bool)).mean()
        print(f"  ORB breakout days={len(o)}  FAILED/trap rate={fail:.1f}%  worked={100-fail:.1f}%")
    nb=orbdf[(orbdf.win==tag)&(orbdf.dir=='none')]
    print(f"  ORB no-breakout days={len(nb)}")
    rv=revdf[revdf.win==tag]
    if len(rv):
        vc=rv.outcome.value_counts(normalize=True)*100
        print(f"  VWAP 2sd stretch events={len(rv)}  revert={vc.get('revert',0):.1f}%  keepgoing={vc.get('keepgoing',0):.1f}%  neither={vc.get('neither',0):.1f}%")
        rvb=rv[rv.side<0]  # below vwap = V9 long setup
        if len(rvb):
            vcb=rvb.outcome.value_counts(normalize=True)*100
            print(f"    (below-VWAP / V9-long setups n={len(rvb)}) revert={vcb.get('revert',0):.1f}% keepgoing={vcb.get('keepgoing',0):.1f}%")

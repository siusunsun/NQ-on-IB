"""_cf_r3.py - anti-overfit stress battery for R3 VWAP RE-ENTRY overlay. READ-ONLY."""
import sys, importlib.util, math
import numpy as np, pandas as pd, pytz
ET=pytz.timezone("America/New_York")
sys.path.insert(0,"/root/v9/bt"); sys.path.insert(0,"/root/v9")
RNG=np.random.default_rng(23)
class _N:
    @staticmethod
    def cdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
    @staticmethod
    def ppf(p):
        a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
        b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
        c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
        d=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]; pl=0.02425
        if p<pl:
            q=math.sqrt(-2*math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        if p<=1-pl:
            q=p-0.5; r=q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
        q=math.sqrt(-2*math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
norm=_N()
def _load(n,p):
    s=importlib.util.spec_from_file_location(n,p); m=importlib.util.module_from_spec(s)
    sys.modules[n]=m; s.loader.exec_module(m); return m
bt=_load("bt_nq","/root/v9/bt/bt_nq.py"); vsl=_load("vsl","/root/v9/v9_strategy_lib.py")
usv=vsl.update_session_vwap; sess_key=vsl.session_for_utc_bar
FULL=20.0

print("Building R3 sleeve ...")
df=bt.load_all(); daily,regime=bt.build_daily_and_regime(df); bars5=bt.build_5min(df)
vt,sigs=bt.run_vwap_tlb(df,bars5,regime); r3=vt["VWAP_LONG_R3"]
n=len(bars5); bts=[b[0] for b in bars5]
o5=np.array([b[1] for b in bars5]); h5=np.array([b[2] for b in bars5])
l5=np.array([b[3] for b in bars5]); c5=np.array([b[4] for b in bars5]); v5=np.array([b[5] for b in bars5])

def build_vwap(K):
    vwap5=np.full(n,np.nan); sd5=np.full(n,np.nan)
    cur=None; cumv=cumvp=cumsq=0.0
    for i in range(n):
        sk=sess_key(bts[i].to_pydatetime(),0)
        if sk!=cur: cur=sk; cumv=cumvp=cumsq=0.0
        cumv,cumvp,cumsq,vw,sd=usv(cumv,cumvp,cumsq,h5[i],l5[i],c5[i],v5[i])
        vwap5[i]=vw; sd5[i]=sd
    return vwap5,sd5,vwap5+K*sd5
VW2,SD2,UP2=build_vwap(2.0)
bucket_idx={bts[i]:i for i in range(n)}
def floor5(ts):
    t=pd.Timestamp(ts); t=t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC"); return t.floor("5min")
def et_min(ts): e=ts.tz_convert(ET); return e.hour*60+e.minute
FLAT=15*60+55
idx1=df.index; o1=df["open"].values;h1=df["high"].values;l1=df["low"].values;c1=df["close"].values

def run_overlay(wait=2, stop_mode='sig', tp_force=None, K=2.0, cost_frac=0.0, flat=FLAT):
    if K==2.0: vwap5,upper5=VW2,UP2
    else:
        vw,sd,up=build_vwap(K); vwap5,upper5=vw,up
    fires=[]
    for tr in r3:
        if tr["exit_reason"]!="STOP": continue
        sig_bucket=floor5(tr["entry_utc"]-pd.Timedelta(minutes=5)); stop_bucket=floor5(tr["exit_utc"])
        si=bucket_idx.get(sig_bucket); ki=bucket_idx.get(stop_bucket)
        if si is None or ki is None: continue
        sess=sess_key(bts[ki].to_pydatetime(),0); entry=None; j=ki+wait
        while j<n and sess_key(bts[j].to_pydatetime(),0)==sess and et_min(bts[j])<flat:
            runmax=h5[ki:j].max() if j>ki else h5[ki]
            if h5[j]>runmax:
                fill=max(o5[j],runmax)
                if stop_mode=='sig': stop_lvl=l5[si:j].min()
                elif stop_mode=='stopbkt': stop_lvl=l5[ki:j].min()
                else: stop_lvl=l5[max(si,j-6):j].min()
                lag_vw=vwap5[j-1]; lag_up=upper5[j-1]
                if np.isnan(lag_vw): j+=1; continue
                if tp_force=='VWAP': tp_kind='VWAP'
                elif tp_force=='UPPER': tp_kind='UPPER'
                else: tp_kind="VWAP" if fill<lag_vw else "UPPER"
                riskpt=fill-stop_lvl
                if riskpt<=0: entry="BAD"; break
                entry=dict(j=j,fill=fill,stop=stop_lvl,tp_kind=tp_kind,riskpt=riskpt,sess=sess); break
            j+=1
        if entry in (None,"BAD"): continue
        j=entry["j"]; fill=entry["fill"]; stop_lvl=entry["stop"]; kind=entry["tp_kind"]
        start=bts[j]+pd.Timedelta(minutes=5)
        m0=np.searchsorted(idx1.values,np.datetime64(start.tz_convert("UTC").tz_localize(None)))
        ex_px=None; ex_reason=None; m=m0
        while m<len(idx1):
            t=idx1[m]
            if sess_key(t.to_pydatetime(),0)!=entry["sess"]: ex_px=c1[m-1] if m>m0 else fill; ex_reason="SESS"; break
            if et_min(t)>=flat: ex_px=c1[m]; ex_reason="FLAT"; break
            if l1[m]<=stop_lvl: ex_px=stop_lvl; ex_reason="STOP"; break
            b=floor5(t); bi=bucket_idx.get(b-pd.Timedelta(minutes=5))
            lvl=(vwap5[bi] if kind=="VWAP" else upper5[bi]) if bi is not None else np.nan
            if not np.isnan(lvl) and lvl>fill and h1[m]>=lvl: ex_px=lvl; ex_reason="TARGET"; break
            m+=1
        if ex_px is None: ex_px=c1[-1]; ex_reason="END"
        pts=ex_px-fill-(cost_frac*fill if cost_frac>0 else 0.0); R=(ex_px-fill)/entry["riskpt"]
        fires.append(dict(entry_utc=bts[j]+pd.Timedelta(minutes=5),pts=pts,R=R,riskpt=entry["riskpt"],reason=ex_reason))
    return fires

def summ(fires):
    pts=np.array([f["pts"] for f in fires]); R=np.array([f["R"] for f in fires])
    return len(fires),R.mean(),(pts>0).mean()*100,pts.sum()*FULL

base=run_overlay()
nB,eR,win,net=summ(base)
print(f"\nBASELINE: n={nB} expR={eR:+.3f} win={win:.1f}pct net(full)=${net:,.0f}")

print("\n--- 1. PARAMETER SENSITIVITY (n / expR / net$) ---")
print("TP magnet (K band + forced kind):")
for K in [1.5,2.0,2.5]:
    f=run_overlay(K=K); nn,e,w,nt=summ(f); print(f"  K={K}: n={nn} expR={e:+.3f} net=${nt:,.0f}")
for tf in ['VWAP','UPPER',None]:
    f=run_overlay(tp_force=tf); nn,e,w,nt=summ(f); print(f"  tp_force={str(tf):5s}: n={nn} expR={e:+.3f} net=${nt:,.0f}")
print("earliest-trigger wait (bars after stop bucket):")
for w in [1,2,3]:
    f=run_overlay(wait=w); nn,e,ww,nt=summ(f); print(f"  wait={w}: n={nn} expR={e:+.3f} net=${nt:,.0f}")
print("stop definition:")
for sm in ['sig','stopbkt','recent6']:
    f=run_overlay(stop_mode=sm); nn,e,w,nt=summ(f); print(f"  stop={sm:8s}: n={nn} expR={e:+.3f} net=${nt:,.0f}")
print("flatten time:")
for fl in [14*60+55,15*60+55]:
    f=run_overlay(flat=fl); nn,e,w,nt=summ(f); print(f"  flat={fl//60}:{fl%60:02d}: n={nn} expR={e:+.3f} net=${nt:,.0f}")

print("\n--- 3. COST SENSITIVITY ---")
for mult,lbl in [(0.0,'0x'),(0.00015,'1x'),(0.0003,'2x'),(0.00045,'3x')]:
    f=run_overlay(cost_frac=mult); pts=np.array([x['pts'] for x in f])
    print(f"  {lbl:4s}: n={len(f)} expPts={pts.mean():+.2f} net=${pts.sum()*FULL:,.0f}")

print("\n--- 4. SUB-PERIOD (per-year expR) ---")
yr=pd.Series([pd.Timestamp(f['entry_utc']).tz_convert(ET).year for f in base])
Rs=pd.Series([f['R'] for f in base]); ptss=pd.Series([f['pts'] for f in base])
for y in sorted(yr.unique()):
    m=yr==y; print(f"  {y}: n={m.sum():2d} expR={Rs[m].mean():+.3f} net=${ptss[m].sum()*FULL:,.0f}")
half=len(base)//2
print(f"  IS(1st half) net=${ptss[:half].sum()*FULL:,.0f}  OOS(2nd half) net=${ptss[half:].sum()*FULL:,.0f}")

print("\n--- 2. PERMUTATION NULL (random-entry within same session/window) ---")
# for each fire session, pick random 5m bar after ki within window; apply VWAP-target/stop leg
real_totpts=sum(f['pts'] for f in base)
# collect (si,ki,sess) contexts of STOP-triggered R3
ctx=[]
for tr in r3:
    if tr["exit_reason"]!="STOP": continue
    si=bucket_idx.get(floor5(tr["entry_utc"]-pd.Timedelta(minutes=5))); ki=bucket_idx.get(floor5(tr["exit_utc"]))
    if si is None or ki is None: continue
    sess=sess_key(bts[ki].to_pydatetime(),0)
    cand=[j for j in range(ki+2,n) if sess_key(bts[j].to_pydatetime(),0)==sess and et_min(bts[j])<FLAT]
    if len(cand)>0: ctx.append((si,ki,sess,cand))
def rand_leg(si,j,sess,kind='VWAP'):
    fill=o5[j]; stop_lvl=l5[si:j].min() if j>si else l5[j]-1
    if fill-stop_lvl<=0: return None
    start=bts[j]+pd.Timedelta(minutes=5)
    m0=np.searchsorted(idx1.values,np.datetime64(start.tz_convert("UTC").tz_localize(None))); m=m0
    while m<len(idx1):
        t=idx1[m]
        if sess_key(t.to_pydatetime(),0)!=sess: return c1[m-1]-fill if m>m0 else 0.0
        if et_min(t)>=FLAT: return c1[m]-fill
        if l1[m]<=stop_lvl: return stop_lvl-fill
        b=floor5(t); bi=bucket_idx.get(b-pd.Timedelta(minutes=5))
        lvl=VW2[bi] if bi is not None else np.nan
        if not np.isnan(lvl) and lvl>fill and h1[m]>=lvl: return lvl-fill
        m+=1
    return c1[-1]-fill
null=[]
for _ in range(150):
    tot=0.0
    for (si,ki,sess,cand) in ctx:
        j=RNG.choice(cand); r=rand_leg(si,j,sess)
        if r is not None: tot+=r
    null.append(tot*FULL)
null=np.array([x for x in null]); pct=(null<real_totpts*FULL).mean()*100
print(f"  real net=${real_totpts*FULL:,.0f}  vs 150 random: mean=${null.mean():,.0f} sd=${null.std():,.0f} -> {pct:.0f}th pctile")

print("\n--- 5. DEFLATED SHARPE (per-trade R series) ---")
Rv=np.array([f['R'] for f in base]); T=len(Rv); sr=Rv.mean()/Rv.std()
srs=[]
for K in [1.5,2.0,2.5]:
    for w in [1,2,3]:
        for sm in ['sig','stopbkt','recent6']:
            f=run_overlay(wait=w,stop_mode=sm,K=K); rr=np.array([x['R'] for x in f])
            srs.append(rr.mean()/rr.std() if len(rr)>2 and rr.std()>0 else 0.0)
srs=np.array(srs); Ntr=len(srs)
sk=((Rv-Rv.mean())**3).mean()/Rv.std()**3; ku=((Rv-Rv.mean())**4).mean()/Rv.std()**4
gamma=0.5772156649; varSR=srs.var()
emax=np.sqrt(varSR)*((1-gamma)*norm.ppf(1-1.0/Ntr)+gamma*norm.ppf(1-1.0/(Ntr*np.e)))
denom=np.sqrt(1-sk*sr+(ku-1)/4.0*sr**2)
dsr=norm.cdf((sr-emax)*np.sqrt(T-1)/denom)
print(f"  per-trade SR(R)={sr:.3f} T={T} skew={sk:.2f} kurt={ku:.2f}")
print(f"  N_trials={Ntr} sd(SR)={np.sqrt(varSR):.3f} E[maxSR]={emax:.3f}")
print(f"  DSR={dsr:.3f} -> {'PASS' if dsr>0.95 else 'FAIL'}")
# save daily R series for correlation
rows=[{'date':pd.Timestamp(f['entry_utc']).tz_convert(ET).date(),'R':f['R'],'pts':f['pts']} for f in base]
pd.DataFrame(rows).to_csv('/root/v9/bt/_cf_r3_daily.csv',index=False)
print("wrote _cf_r3_daily.csv")

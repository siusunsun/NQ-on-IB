import json, numpy as np, pandas as pd
np.random.seed(42)
BT="/root/v9/bt/"
vw=pd.read_csv(BT+"trades_VWAP_LONG_R3.csv"); tl=pd.read_csv(BT+"trades_TLB_LONG.csv"); orb=pd.read_csv(BT+"trades_ORB.csv")
print("=== harness sleeve R distributions ===")
for nm,dfx in [("VWAP_LONG_R3",vw),("TLB_LONG",tl)]:
    R=dfx.R.values
    print(f"{nm}: n={len(R)} meanR={R.mean():.3f} winrate={100*(R>0).mean():.1f}% avgWin={R[R>0].mean():.2f} avgLoss={R[R<=0].mean():.2f} PF={R[R>0].sum()/abs(R[R<=0].sum()):.2f}")
    print(f"   loser R pctiles: min={R.min():.2f} p5={np.percentile(R,5):.2f}  #(R<-1.05)={int((R<-1.05).sum())}/{ (R<0).sum()} losers")

# ---- Live 12 trades (through 08-05, excl 06-24 TLB bug) ----
live_R=[2.974,-1.066,0.414,0.627,-1.075,-1.012,-1.260,2.945,-1.005,-0.920,-1.170,0.195]
live_R=np.array(live_R); print("\n=== LIVE 12 ===")
print(f"sumR={live_R.sum():.3f} meanR={live_R.mean():.3f} winrate={100*(live_R>0).mean():.1f}% ({(live_R>0).sum()}/12)")

# ---- Bootstrap: match composition 8 VWAP + 4 TLB ----
Rvw=vw.R.values; Rtl=tl.R.values
N=200000
samp_sum=np.array([np.random.choice(Rvw,8).sum()+np.random.choice(Rtl,4).sum() for _ in range(N)])
p_sum=(samp_sum<=live_R.sum()).mean()
print(f"\n[composition-matched 8VWAP+4TLB] bootstrap mean sumR={samp_sum.mean():.2f} sd={samp_sum.std():.2f}")
print(f"   P(sumR <= {live_R.sum():.2f}) = {p_sum:.3f}")
# win-rate distribution
samp_w=np.array([ (np.r_[np.random.choice(Rvw,8),np.random.choice(Rtl,4)]>0).sum() for _ in range(50000)])
print(f"   expected wins/12: mean={samp_w.mean():.2f}; P(wins<=5)={ (samp_w<=5).mean():.3f}")
# pooled draw (ignore composition)
pool=np.r_[Rvw,Rtl]
samp2=np.array([np.random.choice(pool,12).sum() for _ in range(N)])
print(f"[pooled] mean sumR={samp2.mean():.2f} sd={samp2.std():.2f} P(sumR<=-0.35)={ (samp2<=live_R.sum()).mean():.3f}")

# ---- Slippage/stop check: live stop R vs harness stop R ----
print("\n=== stop-exit R: live vs harness ===")
live_stops=live_R[live_R<0]
print(f"live losers meanR={live_stops.mean():.3f} (n={len(live_stops)}), values={sorted(np.round(live_stops,2))}")
for nm,dfx in [("VWAP_LONG_R3",vw),("TLB_LONG",tl)]:
    s=dfx[dfx.exit_reason=="STOP"].R
    print(f"{nm} STOP exits meanR={s.mean():.3f} n={len(s)} frac beyond -1.05={100*(s<-1.05).mean():.1f}%")

# ================= PART 3 ORB decay =================
print("\n\n########## PART 3 ORB harness decay ##########")
orb["dt"]=pd.to_datetime(orb.entry_time_et); orb["ym"]=orb.dt.dt.strftime("%Y-%m")
orb["half"]=orb.dt.dt.year.astype(str)+"-H"+((orb.dt.dt.month>6).astype(int)+1).astype(str)
def stats(g):
    R=g.R.values; w=R[R>0]; l=R[R<=0]
    return pd.Series({"n":len(R),"sumR":R.sum(),"meanR":R.mean(),"win%":100*(R>0).mean(),
        "avgWin":w.mean() if len(w) else 0,"avgLoss":l.mean() if len(l) else 0,
        "PF":(w.sum()/abs(l.sum())) if len(l) and l.sum()!=0 else np.nan})
print("span:",orb.dt.min(),"->",orb.dt.max(),"n=",len(orb))
print("\n-- by half-year --")
print(orb.groupby("half").apply(stats).round(2).to_string())
print("\n-- by month 2026 --")
o26=orb[orb.dt.dt.year==2026]
print(o26.groupby("ym").apply(stats).round(2).to_string())

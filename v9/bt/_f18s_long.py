import sys; sys.path.insert(0,'/root/v9/bt')
import _f18s_engine as E
from copy import deepcopy
E.load_bars(5)  # warm cache once

def run(cfg,tf=5):
    tr,so=E.simulate(cfg,tf); return E.metrics(tr),*[E.metrics(x) for x in E.split_trades(tr)]
def fmt(m):
    if m.get('n',0)==0: return "n=0"
    return (f"n={m['n']:<4} win={m['win']:>4}% PFn={m['pf_net']:>5} expR={m['exp_R']:>7} "
            f"exp$={m['exp_net']:>7} net$={m['net_total']:>8.0f} DD={m['maxdd']:>8.0f} bars={m['avg_bars']:>5} TP/SL={m['nTP']}/{m['nSL']}")
def axis(title,cfgs,tf=5):
    print("\n=== "+title+" ===")
    for label,cfg in cfgs:
        m,trn,tst=run(cfg,tf)
        print(f"{label:<20} F {fmt(m)}")
        print(f"{'':<20} T {fmt(trn)}")
        print(f"{'':<20} t {fmt(tst)}")

D=deepcopy(E.DEFAULT); D['direction']='long'
def c(**kw): x=deepcopy(D); x.update(kw); return x

axis("LONG rrTarget",[(f"rr={r}",c(rr=r)) for r in (1,1.5,2,2.5,3,4,5)])
axis("LONG rsiLength",[(f"rsiLen={r}",c(rsiLength=r)) for r in (7,9,14,21)])
sm=[("None",c(smoothMode="None"))]
for mode in("EMA","SMA","RMA"):
    for sl in(3,5,9): sm.append((f"{mode}{sl}",c(smoothMode=mode,smoothLen=sl)))
axis("LONG smoothing",sm)
axis("LONG exLow (short off)",[(f"exLow={lo}",c(exLow=lo)) for lo in (15,20,25,30,35)])
slc=[(f"ATRx{m}",c(slMode="ATR",atrMult=m)) for m in (0.5,1.0,1.5,2.0)]
slc+=[("SignalCandle",c(slMode="SIG")),("Percent0.5%",c(slMode="PCT",pctSL=0.005))]
axis("LONG SL mode",slc)

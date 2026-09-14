import sys; sys.path.insert(0,'/root/v9/bt')
import _f18s_engine as E
from copy import deepcopy

def run(cfg,tf=5):
    tr,so=E.simulate(cfg,tf)
    m=E.metrics(tr); trn,tst=E.split_trades(tr)
    return m,E.metrics(trn),E.metrics(tst),so

def fmt(m):
    if m.get('n',0)==0: return f"n=0"
    return (f"n={m['n']:<4} win={m['win']:>4}% PFn={m['pf_net']:>5} "
            f"expR={m['exp_R']:>7} exp$={m['exp_net']:>7} net$={m['net_total']:>8.0f} "
            f"DD={m['maxdd']:>8.0f} bars={m['avg_bars']:>5} TP/SL={m['nTP']}/{m['nSL']}")

def axis(title, cfgs):
    print("\n=== "+title+" ===")
    for label,cfg in cfgs:
        m,trn,tst,so=run(cfg)
        print(f"{label:<22} FULL {fmt(m)}")
        print(f"{'':<22} TRN  {fmt(trn)}")
        print(f"{'':<22} TST  {fmt(tst)}")

D=E.DEFAULT
def c(**kw): x=deepcopy(D); x.update(kw); return x

if __name__=="__main__":
    which=sys.argv[1] if len(sys.argv)>1 else "all"
    if which in("all","rr"):
        axis("rrTarget (default 20/80 EMA5 ATR1.0 both)",
            [(f"rr={r}",c(rr=r)) for r in (1,1.5,2,3,5)])
    if which in("all","rsilen"):
        axis("rsiLength", [(f"rsiLen={r}",c(rsiLength=r)) for r in (7,9,14,21)])
    if which in("all","smooth"):
        cfgs=[("smooth=None",c(smoothMode="None"))]
        for mode in("EMA","SMA","RMA"):
            for sl in(3,5,9):
                cfgs.append((f"{mode}{sl}",c(smoothMode=mode,smoothLen=sl)))
        axis("smoothing",cfgs)
    if which in("all","levels"):
        axis("extreme levels (long/short)",
            [(f"{lo}/{hi}",c(exLow=lo,exHigh=hi)) for lo,hi in ((15,85),(20,80),(25,75),(30,70))])
    if which in("all","sl"):
        cfgs=[(f"ATRx{m}",c(slMode="ATR",atrMult=m)) for m in (0.5,1.0,1.5,2.0)]
        cfgs.append(("SignalCandle",c(slMode="SIG")))
        cfgs.append(("Percent0.5%",c(slMode="PCT",pctSL=0.005)))
        axis("SL mode",cfgs)
    if which in("all","dir"):
        axis("direction",[(d,c(direction=d)) for d in ("long","short","both")])

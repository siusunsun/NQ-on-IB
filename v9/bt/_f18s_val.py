import sys; sys.path.insert(0,'/root/v9/bt')
import _f18s_engine as E
from copy import deepcopy
E.load_bars(5); E.load_bars(15)
D=deepcopy(E.DEFAULT); D['direction']='long'
def c(**kw): x=deepcopy(D); x.update(kw); return x
def line(tag,m):
    if m.get('n',0)==0: print(f"  {tag}: n=0"); return
    print(f"  {tag}: n={m['n']:<4} win={m['win']:>4}% PFn={m['pf_net']:>5} expR={m['exp_R']:>7} exp$={m['exp_net']:>7} net$={m['net_total']:>7.0f} DD={m['maxdd']:>7.0f} bars={m['avg_bars']} TP/SL={m['nTP']}/{m['nSL']}")
def report(name,cfg,tf):
    tr,so=E.simulate(cfg,tf); trn,tst=E.split_trades(tr)
    print(f"\n{name}  [{tf}min]  still_open={so}")
    line("FULL",E.metrics(tr)); line("TRN ",E.metrics(trn)); line("TST ",E.metrics(tst))

CANDS={
 "A: exLow20 rr5 SMA9 (best-train-exp)": c(exLow=20,rr=5,smoothMode="SMA",smoothLen=9),
 "B: exLow30 rr3 EMA5 (robust large-n)": c(exLow=30,rr=3,smoothMode="EMA",smoothLen=5),
 "C: exLow20 rr5 EMA5 (pine default long)": c(exLow=20,rr=5,smoothMode="EMA",smoothLen=5),
 "D: exLow35 rr3 EMA5 (loosest)": c(exLow=35,rr=3,smoothMode="EMA",smoothLen=5),
}
for name,cfg in CANDS.items():
    report(name,cfg,5)
    report(name,cfg,15)

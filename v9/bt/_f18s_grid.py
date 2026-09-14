import sys; sys.path.insert(0,'/root/v9/bt')
import _f18s_engine as E
from copy import deepcopy
E.load_bars(5)
D=deepcopy(E.DEFAULT); D['direction']='long'
def c(**kw): x=deepcopy(D); x.update(kw); return x

rows=[]
NCFG=0
for exLow in (20,25,30,35):
    for rr in (2,3,4,5):
        for sm,sl in (("None",5),("EMA",3),("EMA",5),("SMA",9)):
            cfg=c(exLow=exLow,rr=rr,smoothMode=sm,smoothLen=sl)
            tr,so=E.simulate(cfg)
            trn,tst=E.split_trades(tr)
            mt=E.metrics(trn); ms=E.metrics(tst); mf=E.metrics(tr)
            NCFG+=1
            if mt.get('n',0)==0: continue
            rows.append(dict(exLow=exLow,rr=rr,sm=f"{sm}{sl}" if sm!="None" else "None",
                ntr=mt['n'],expT=mt['exp_net'],pfT=mt['pf_net'],netT=mt['net_total'],
                nts=ms.get('n',0),expS=ms.get('exp_net',0),pfS=ms.get('pf_net',0),netS=ms.get('net_total',0),
                nf=mf['n'],pfF=mf['pf_net'],netF=mf['net_total'],ddF=mf['maxdd'],winF=mf['win'],expRF=mf['exp_R']))

print(f"TOTAL CONFIGS in this grid: {NCFG}")
# filter: require decent train sample and TEST also has trades
elig=[r for r in rows if r['ntr']>=40 and r['nts']>=10]
print(f"eligible (ntrain>=40 & ntest>=10): {len(elig)}")
print("\n--- TOP 12 by TRAIN exp_net$/trade (eligible) ---")
hdr="exLow rr  sm     |ntrTRN expT  pfT   netT  |ntTST expS  pfS   netS  |nFULL pfF  netF  DD"
print(hdr)
for r in sorted(elig,key=lambda x:-x['expT'])[:12]:
    print(f"{r['exLow']:>4} {r['rr']:>3} {r['sm']:<6}|{r['ntr']:>5} {r['expT']:>6} {r['pfT']:>5} {r['netT']:>6.0f} |{r['nts']:>4} {r['expS']:>6} {r['pfS']:>5} {r['netS']:>6.0f} |{r['nf']:>4} {r['pfF']:>5} {r['netF']:>6.0f} {r['ddF']:>7.0f}")
print("\n--- TOP 12 by TRAIN PF_net (eligible) ---")
print(hdr)
for r in sorted(elig,key=lambda x:-x['pfT'])[:12]:
    print(f"{r['exLow']:>4} {r['rr']:>3} {r['sm']:<6}|{r['ntr']:>5} {r['expT']:>6} {r['pfT']:>5} {r['netT']:>6.0f} |{r['nts']:>4} {r['expS']:>6} {r['pfS']:>5} {r['netS']:>6.0f} |{r['nf']:>4} {r['pfF']:>5} {r['netF']:>6.0f} {r['ddF']:>7.0f}")
print("\n--- how many eligible survive TEST net>0 ---")
surv=[r for r in elig if r['netS']>0]
print(f"{len(surv)}/{len(elig)} eligible configs have TEST net$>0")

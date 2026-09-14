import pickle, numpy as np
rows=pickle.load(open("/root/v9/bt/_f19_rows.pkl","rb"))
# distribution of bScore,rScore,bias across TRADE entry bars
from collections import Counter
bc=Counter(); rc=Counter(); bias=Counter(); diff=Counter()
for r in rows:
    s=r["sig19"]; bc[s["bScore"]]+=1; rc[s["rScore"]]+=1; bias[s["bias"]]+=1
    diff[s["bScore"]-s["rScore"]]+=1
print("bScore dist:",dict(sorted(bc.items())))
print("rScore dist:",dict(sorted(rc.items())))
print("bScore-rScore dist:",dict(sorted(diff.items())))
print("bias dist:",dict(bias))
# also show a few sample snaps
for r in rows[:5]:
    s=r["sig19"]; print(r["sleeve"],r["direction"],"b=",s["bScore"],"r=",s["rScore"],"bias=",s["bias"],
          "adx=%.1f"%s["adx"],"rsi=%.1f"%s["rsi14"],"c-vwap=%.1f"%(s["close"]-s["vwap"]))

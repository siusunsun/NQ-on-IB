import sys; sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _cs_lib import *
from _cs_regime import build
pd.set_option('display.width',220)
F=build()
D=pd.read_csv('/root/v9/bt/_cs_trades_deployed.csv')
J=D.join(F,on='dord')
print("regime coverage: er20 non-null on trades:",J['er20'].notna().sum(),"/",len(J))
print("\n--- daily ER20 by year (mean of causal value) ---")
Fy=F.copy(); Fy['yr']=[pd.Timestamp.fromordinal(i).year for i in Fy.index]
print(Fy.groupby('yr')[['er5','er10','er20','adx14','brk20']].mean().round(3).to_string())
for feat in ['er5','er10','er20','adx14','brk20']:
    sub=J[J[feat].notna()]
    q=sub[feat].quantile([1/3,2/3]).values
    b=pd.cut(sub[feat],[-np.inf,q[0],q[1],np.inf],labels=['chop','mid','trend'])
    print(f"\n=== {feat} terciles (FULL-SAMPLE cut, descriptive) ===")
    print(sub.groupby(b,observed=False).agg(n=('R','size'),expR=('R','mean'),
         net=('netpts',lambda x: round(x.sum()*2)),win=('netpts',lambda x:round((x>0).mean()*100,1))).to_string())

import sys; sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _cs_lib import *
rows=[]
for lvl in (80,100):
  for sep in (0.0005,0.0015):
    for k in (0.5,0.75,1.0,1.25,1.5,2.0,99.0):
      for slope in (True,False):
        T=scan(lvl,sep,k,need_slope=slope)
        if len(T)==0: continue
        rows.append(dict(lvl=lvl,sep=sep,k=k,slope=slope,n=len(T),
           expR=round(T['R'].mean(),3),net=round(T['netpts'].sum()*PT_USD)))
R=pd.DataFrame(rows).sort_values('n')
pd.set_option('display.max_rows',200); pd.set_option('display.width',200)
print(R.to_string(index=False))

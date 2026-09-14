import sys, resource
sys.path.insert(0,'/root/v9/bt')
import numpy as np, pandas as pd
from _cs_lib import *
pd.set_option('display.width',200)
def rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.

print("AM candidate rows:",len(AM),"days:",len(np.unique(AMday)),"RSS %.0fMB"%rss())
D=scan(100.0,0.0015,1.0)
print("\n=== DEPLOYED CELL (lvl100 sep0.15%% k1.0) ===")
print(scorecard(D,'deployed'))
print("yearly:",yearly(D))
D.to_csv('/root/v9/bt/_cs_trades_deployed.csv',index=False)
L=scan(80.0,0.0005,0.5)
print("\n=== LOOSE CELL (lvl80 sep0.05%% k0.5) ===")
print(scorecard(L,'loose'))
print("yearly:",yearly(L))
L.to_csv('/root/v9/bt/_cs_trades_loose.csv',index=False)
print("RSS %.0fMB"%rss())

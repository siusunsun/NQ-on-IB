import glob,sys,warnings; warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import numpy as np, pandas as pd
BT='/root/v9/bt/'; NQ_DIR='/root/v9/data_1m/NQ/'; COST_FRAC=0.00015

# ---- snapback daily POINTS P&L (1 MNQ, midnight anchor) ----
def load_1m():
    parts=[pd.read_csv(f,usecols=['time','close']) for f in sorted(glob.glob(NQ_DIR+'*.csv'))]
    d=pd.concat(parts,ignore_index=True)
    d['time']=pd.to_datetime(d['time'],utc=True,errors='coerce')
    return d.dropna(subset=['time']).drop_duplicates('time').set_index('time').sort_index().tz_convert('America/New_York')['close']
c1m=load_1m()
day=c1m.groupby(c1m.index.normalize()).last(); day.index=day.index.tz_localize(None); day=day.dropna()
z=(day-day.rolling(20).mean())/day.rolling(20).std(ddof=0)
df=pd.DataFrame({'c':day,'z':z}).dropna()
c=df['c'].values; zv=df['z'].values
dpts=np.r_[0.0,np.diff(c)]                         # C_t - C_{t-1} in points
pos=(zv<-1.5).astype(float); expo=np.r_[0.0,pos[:-1]]
dp=np.abs(np.diff(np.r_[0.0,expo]))
costpts=(COST_FRAC/2)*c*dp                          # 0.75bp of price per position change
snap_pts=pd.Series(expo*dpts-costpts, index=df.index.date)
snap_pts.index=pd.to_datetime(snap_pts.index).date

# ---- book sleeves daily points ----
def v9daily(fn):
    d=pd.read_csv(BT+fn); d['dt']=pd.to_datetime(d['entry_time_et']).dt.date
    return d.groupby('dt')['pnl_points'].sum()
VL=v9daily('trades_VWAP_LONG_R3.csv'); VS=v9daily('trades_VWAP_SHORT_R1.csv'); TL=v9daily('trades_TLB_LONG.csv')
cci=pd.read_csv(BT+'_cci_trades.csv'); cci['dt']=pd.to_datetime(cci['date']).dt.date
cci['wpts']=cci['pts']*np.where(cci['slot']=='AM',1.0,0.33)
CCI=cci.groupby('dt')['wpts'].sum()

start=max(VL.index.min(),VS.index.min(),TL.index.min())
idx=sorted(set(VL.index)|set(VS.index)|set(TL.index)|set(snap_pts.index)|set(CCI.index))
idx=[d for d in idx if d>=start and d<=snap_pts.index.max()]
I=pd.Index(idx,name='dt')
def al(s): return s.reindex(I).fillna(0.0)
VLa,VSa,TLa,SNa,CCa=al(VL),al(VS),al(TL),al(snap_pts),al(CCI)
V9=VLa+VSa+TLa
BOOK=V9+CCa                                          # full book incl paper-candidate CCI

def sharpe(x): x=np.asarray(x,float); sd=x.std(ddof=1); return x.mean()/sd*np.sqrt(252) if sd>0 else float('nan')
def maxdd(x): cu=np.cumsum(x); pk=np.maximum.accumulate(cu); return (cu-pk).min()
def retdd(x): dd=maxdd(x); return np.sum(x)/abs(dd) if dd<0 else float('nan')
def line(x,l): x=np.asarray(x,float); print(f'  {l:30s} netpts={x.sum():8.0f} Sharpe={sharpe(x):5.2f} maxDD={maxdd(x):8.0f} ret/DD={retdd(x):5.2f} dailySD={x.std():.1f}')

print('aligned days',len(I),I[0],'->',I[-1])
print('snapback active days in window:',int((SNa!=0).sum()),f'({(SNa!=0).mean()*100:.0f}%)')
print('\n--- standalone series (daily points, 1 contract) ---')
for s,l in [(SNa,'SNAPBACK'),(VLa,'VWAP_LONG_R3'),(VSa,'VWAP_SHORT_R1'),(TLa,'TLB_LONG'),(V9,'V9 (3 sleeves)'),(CCa,'CCI (paper cand)'),(BOOK,'BOOK=V9+CCI')]:
    line(s,l)

print('\n--- CORRELATION of Snapback daily pts vs book components ---')
for s,l in [(VLa,'VWAP_LONG_R3'),(VSa,'VWAP_SHORT_R1'),(TLa,'TLB_LONG'),(V9,'V9_total'),(CCa,'CCI'),(BOOK,'BOOK(V9+CCI)')]:
    allc=pd.Series(SNa.values).corr(pd.Series(s.values))
    act=SNa!=0
    actc=pd.Series(SNa[act].values).corr(pd.Series(s[act].values))
    print(f'  {l:16s} corr(all)={allc:+.3f}  corr(active)={actc:+.3f}')

print('\n--- INCREMENTAL: add Snapback to V9 book at vol-scaled weights ---')
base=V9
sd_book=base.std(); sd_sn=SNa.std()
w_eqvol=sd_book/sd_sn                                # equal-vol contract multiplier
print(f'  V9 dailySD={sd_book:.1f}  Snapback dailySD={sd_sn:.1f}  equal-vol weight={w_eqvol:.3f} contracts')
line(base,'V9 alone')
for name,w in [('equal-vol',w_eqvol),('half-vol',w_eqvol/2),('third-vol',w_eqvol/3),('1 contract',1.0)]:
    line(base+w*SNa, f'V9 + {name} snapback')
print('\n--- INCREMENTAL: add Snapback to FULL book (V9+CCI) ---')
base=BOOK; sd_book=base.std(); w_eqvol=sd_book/sd_sn
print(f'  BOOK dailySD={sd_book:.1f}  equal-vol weight={w_eqvol:.3f}')
line(base,'BOOK alone')
for name,w in [('equal-vol',w_eqvol),('half-vol',w_eqvol/2),('third-vol',w_eqvol/3)]:
    line(base+w*SNa, f'BOOK + {name} snapback')

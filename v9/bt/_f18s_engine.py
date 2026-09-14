"""#18 'RSI Entry Engine (trade_w_samet)' — standalone MR strategy port for NQ.
Read-only research scratch. Pine v6 spec ported exactly. Single-position-overall model."""
import sys, importlib.util
import numpy as np, pandas as pd

sys.path.insert(0, "/root/v9/bt")
spec = importlib.util.spec_from_file_location("bt_nq", "/root/v9/bt/bt_nq.py")
bt = importlib.util.module_from_spec(spec); sys.modules["bt_nq"] = bt; spec.loader.exec_module(bt)

TICK = 0.25
PT_USD = 2.0            # MNQ $2/pt, qty=1
COST_RT = 2.04         # commission 1.04 rt + 1 tick slip/side (0.25pt=$0.50) x2 = 1.00 -> 2.04

_CACHE = {}
def load_bars(tf=5):
    key = ("bars", tf)
    if key in _CACHE: return _CACHE[key]
    df = bt.load_all(); bars5 = bt.build_5min(df)
    ts = np.array([b[0] for b in bars5])
    O = np.array([b[1] for b in bars5]); H = np.array([b[2] for b in bars5])
    L = np.array([b[3] for b in bars5]); C = np.array([b[4] for b in bars5]); V = np.array([b[5] for b in bars5])
    if tf == 15:
        idx = pd.DatetimeIndex(pd.to_datetime(list(ts), utc=True))
        d = pd.DataFrame({'o':O,'h':H,'l':L,'c':C,'v':V}, index=idx)
        g = d.resample('15min', label='left', closed='left').agg(
            {'o':'first','h':'max','l':'min','c':'last','v':'sum'}).dropna(subset=['c'])
        ts=g.index.to_numpy(); O=g['o'].to_numpy(); H=g['h'].to_numpy()
        L=g['l'].to_numpy(); C=g['c'].to_numpy(); V=g['v'].to_numpy()
    out=(ts,O,H,L,C,V)
    _CACHE[key]=out
    return out

def rma(x,p):
    out=np.full(len(x),np.nan)
    if len(x)<p: return out
    out[p-1]=x[:p].mean()
    for i in range(p,len(x)): out[i]=(out[i-1]*(p-1)+x[i])/p
    return out
def ema(x,p):
    a=2/(p+1); out=np.full(len(x),np.nan)
    if len(x)<p: return out
    out[p-1]=x[:p].mean()
    for i in range(p,len(x)): out[i]=a*x[i]+(1-a)*out[i-1]
    return out
def sma(x,p): return pd.Series(x).rolling(p).mean().values
def rsi(x,p):
    d=np.diff(x,prepend=x[0]); up=np.where(d>0,d,0.0); dn=np.where(d<0,-d,0.0)
    ru=rma(up,p); rd=rma(dn,p)
    with np.errstate(divide='ignore',invalid='ignore'):
        rs=ru/rd
    out=100-100/(1+rs)
    out[np.isnan(ru)]=np.nan
    out[(rd==0)&(~np.isnan(ru))]=100.0
    return out
def atr(H,L,C,p):
    prevC=np.concatenate([[C[0]],C[:-1]])
    TR=np.maximum(H-L,np.maximum(np.abs(H-prevC),np.abs(L-prevC)))
    return rma(TR,p)
def ma(x,mode,p):
    if mode=="None": return x
    if mode=="EMA": return ema(x,p)
    if mode=="SMA": return sma(x,p)
    if mode=="RMA": return rma(x,p)
    raise ValueError(mode)
def rsi_smooth(C,rsiLength,smoothMode,smoothLen):
    r=rsi(C,rsiLength)
    if smoothMode=="None": return r
    # apply MA on the valid (non-NaN) slice so RSI warmup NaNs don't poison the recursion
    out=np.full(len(r),np.nan)
    valid=np.where(~np.isnan(r))[0]
    if len(valid)==0: return out
    s=valid[0]
    out[s:]=ma(r[s:],smoothMode,smoothLen)
    return out
def crossover(a,thr):
    out=np.zeros(len(a),bool)
    prev=np.concatenate([[np.nan],a[:-1]])
    m=(~np.isnan(a))&(~np.isnan(prev))
    out[m]=(prev[m]<=thr)&(a[m]>thr)
    return out
def crossunder(a,thr):
    out=np.zeros(len(a),bool)
    prev=np.concatenate([[np.nan],a[:-1]])
    m=(~np.isnan(a))&(~np.isnan(prev))
    out[m]=(prev[m]>=thr)&(a[m]<thr)
    return out

def simulate(cfg, tf=5):
    ts,O,H,L,C,V=load_bars(tf)
    n=len(C)
    rs=rsi_smooth(C,cfg['rsiLength'],cfg['smoothMode'],cfg['smoothLen'])
    up=crossover(rs,cfg['exLow'])
    dn=crossunder(rs,cfg['exHigh'])
    A=atr(H,L,C,cfg.get('atrLen',14)) if cfg['slMode']=='ATR' else None
    epx = C if cfg['entryPx']=='close' else (O if cfg['entryPx']=='open' else (H+L+C)/3.0)
    rr=cfg['rr']; direction=cfg['direction']
    trades=[]; pos=None
    for i in range(n):
        if pos is not None and i>pos['ebar']:
            hi=H[i]; lo=L[i]; res=None; exitp=None
            if pos['side']=='L':
                hit_sl=lo<=pos['sl']; hit_tp=hi>=pos['tp']
                if hit_sl: exitp=pos['sl']; res='SL'
                elif hit_tp: exitp=pos['tp']; res='TP'
            else:
                hit_sl=hi>=pos['sl']; hit_tp=lo<=pos['tp']
                if hit_sl: exitp=pos['sl']; res='SL'
                elif hit_tp: exitp=pos['tp']; res='TP'
            if res is not None:
                pnl=(exitp-pos['entry']) if pos['side']=='L' else (pos['entry']-exitp)
                trades.append(dict(side=pos['side'],entry_ts=ts[pos['ebar']],entry=pos['entry'],
                    exit=exitp,res=res,pnl_pts=pnl,bars=i-pos['ebar'],risk=pos['risk']))
                pos=None
        if pos is None:
            long_sig=up[i] and direction in ('long','both')
            short_sig=dn[i] and direction in ('short','both')
            side='L' if long_sig else ('S' if short_sig else None)
            if side is not None:
                e=epx[i]
                if side=='L':
                    if cfg['slMode']=='ATR': sl=e-A[i]*cfg['atrMult']
                    elif cfg['slMode']=='SIG': sl=L[i]
                    else: sl=e*(1-cfg['pctSL'])
                    if sl!=sl: continue
                    sl=min(sl,e-TICK); risk=e-sl; tp=e+risk*rr
                else:
                    if cfg['slMode']=='ATR': sl=e+A[i]*cfg['atrMult']
                    elif cfg['slMode']=='SIG': sl=H[i]
                    else: sl=e*(1+cfg['pctSL'])
                    if sl!=sl: continue
                    sl=max(sl,e+TICK); risk=sl-e; tp=e-risk*rr
                pos=dict(side=side,ebar=i,entry=e,sl=sl,tp=tp,risk=risk)
    return trades, (1 if pos is not None else 0)

def metrics(trades,label=""):
    if not trades: return dict(label=label,n=0)
    n=len(trades)
    wins=[t for t in trades if t['res']=='TP']; losses=[t for t in trades if t['res']=='SL']
    net=[t['pnl_pts']*PT_USD-COST_RT for t in trades]
    gusd=[t['pnl_pts']*PT_USD for t in trades]
    R=[t['pnl_pts']/t['risk'] if t['risk']>0 else 0 for t in trades]
    gp=sum(x for x in gusd if x>0); gl=-sum(x for x in gusd if x<0)
    np_p=sum(x for x in net if x>0); np_l=-sum(x for x in net if x<0)
    eq=np.cumsum(net); peak=np.maximum.accumulate(eq); dd=eq-peak
    return dict(label=label,n=n,win=round(len(wins)/n*100,1),
        pf_gross=round(gp/gl,3) if gl>0 else float('inf'),
        pf_net=round(np_p/np_l,3) if np_l>0 else float('inf'),
        exp_R=round(np.mean(R),4), exp_net=round(np.mean(net),2),
        net_total=round(sum(net),0), gross_total=round(sum(gusd),0),
        maxdd=round(dd.min(),0), avg_bars=round(np.mean([t['bars'] for t in trades]),1),
        nTP=len(wins),nSL=len(losses))

def split_trades(trades):
    yr=lambda t: pd.Timestamp(t['entry_ts']).year
    return [t for t in trades if yr(t) in (2024,2025)], [t for t in trades if yr(t)==2026]

DEFAULT=dict(rsiLength=14,smoothMode="EMA",smoothLen=5,exLow=20,exHigh=80,
    slMode="ATR",atrLen=14,atrMult=1.0,pctSL=0.005,rr=5.0,direction="both",entryPx="close")

import json, pandas as pd, numpy as np
MNQ=2.0; RT=2.04
m=pd.read_csv("/root/v9/bt/_rec_model.csv")
m["entry_utc"]=pd.to_datetime(m.entry_utc); m["exit_utc"]=pd.to_datetime(m.exit_utc)
LO=pd.Timestamp("2026-06-24",tz="UTC"); HI=pd.Timestamp("2026-08-27",tz="UTC")
m=m[(m.entry_utc>=LO)&(m.entry_utc<HI)].copy()

fills=json.load(open("/root/v9/v9_live_trade_log.json"))["fills"]
for f in fills: f["ts"]=pd.Timestamp(f["time"],tz="UTC")
live=[]; open_={}
for f in fills:
    ins=f["instrument"]
    if f["label"].endswith("_ENTRY"): open_[ins]=f
    else:
        e=open_.pop(ins,None)
        if e is None: print("UNPAIRED EXIT",f); continue
        q=abs(round(f["realized"]/((f["price"]-e["price"])*MNQ))) if f["price"]!=e["price"] else e["qty"]
        live.append(dict(sleeve=ins, en_t=e["ts"], en_px=e["price"], ex_t=f["ts"], ex_px=f["price"],
                         reason=f["label"], realized=f["realized"], R=f["realized_R"], qty=q))
print("live RTs",len(live),"gross",round(sum(x["realized"] for x in live),2))
print("open at end:",list(open_))

def modmatch(sl,t,tol=pd.Timedelta(minutes=6)):
    c=m[(m.sleeve==sl)&((m.entry_utc-t).abs()<=tol)]
    return c.iloc[0] if len(c) else None

rows=[]; used=set()
for lv in sorted(live,key=lambda x:x["en_t"]):
    tlb = lv["sleeve"]=="TLB_LONG"
    sl_asat = "TLB_PIVOT" if tlb else lv["sleeve"]
    sl_now  = "TLB_HYB40" if tlb else lv["sleeve"]
    a=modmatch(sl_asat,lv["en_t"]); b=modmatch(sl_now,lv["en_t"])
    r=dict(date=str(lv["en_t"].date()), sleeve=lv["sleeve"], live_qty=lv["qty"],
           live_en_t=str(lv["en_t"].time()), live_en=lv["en_px"], live_ex_t=str(lv["ex_t"].time()),
           live_ex=lv["ex_px"], live_reason=lv["reason"], live_pts=round(lv["ex_px"]-lv["en_px"],2),
           live_gross=lv["realized"])
    if a is None:
        r.update(cls="PHANTOM/REGIME", mdl_en=None, mdl_ex=None, mdl_reason=None, mdl_pts=None,
                 mdl_gross_asat=0.0, mdl_gross_now=0.0, en_slip=None, ex_slip=None,
                 diff_asat=lv["realized"])
    else:
        used.add(int(a.name));  used.add(int(b.name)) if b is not None else None
        qn = 2 if lv["sleeve"]=="VWAP_LONG_R3" else 1
        ga = round(a.pnl_points*MNQ*lv["qty"],2)
        gn = round((b.pnl_points if b is not None else a.pnl_points)*MNQ*qn,2)
        ensl=round(lv["en_px"]-a.entry_px,2); exsl=round(lv["ex_px"]-a.exit_px,2)
        dt=abs((lv["ex_t"]-a.exit_utc).total_seconds())/60.0
        same = (lv["reason"]==a.exit_reason) or (lv["reason"]=="TARGET" and a.exit_reason in("TARGET",))
        cls = "SLIPPAGE" if (same and dt<=10) else "EXIT_DIFF"
        if lv["reason"]=="EOD" and a.exit_reason=="EOD": cls="SLIPPAGE"
        r.update(cls=cls, mdl_en=a.entry_px, mdl_ex=a.exit_px, mdl_reason=a.exit_reason,
                 mdl_pts=a.pnl_points, mdl_gross_asat=ga, mdl_gross_now=gn,
                 en_slip=ensl, ex_slip=exsl, diff_asat=round(lv["realized"]-ga,2))
    rows.append(r)

# model trades with no live counterpart
for i,t in m.iterrows():
    if i in used: continue
    if t.sleeve=="TLB_HYB40": continue   # pivot is the as-at twin
    qn = 2 if t.sleeve=="VWAP_LONG_R3" else 1
    cls = "SLEEVE_NOT_LIVE" if t.sleeve=="R3_RE" else "MISSED"
    rows.append(dict(date=str(t.entry_utc.date()), sleeve=t.sleeve, live_qty=0,
        live_en_t=None, live_en=None, live_ex_t=None, live_ex=None, live_reason=None,
        live_pts=None, live_gross=0.0, cls=cls, mdl_en=t.entry_px, mdl_ex=t.exit_px,
        mdl_reason=t.exit_reason, mdl_pts=t.pnl_points,
        mdl_gross_asat=round(t.pnl_points*MNQ*qn,2), mdl_gross_now=round(t.pnl_points*MNQ*qn,2),
        en_slip=None, ex_slip=None, diff_asat=round(-t.pnl_points*MNQ*qn,2)))


# --- correction: 2026-06-30 TLB order was genuinely qty=5 (bot log: "placing MKT qty=5"),
# but the bot's realized field booked only 1 lot. Restate live at the true 5 lots.
for r in rows:
    if r['date']=='2026-06-30' and r['sleeve']=='TLB_LONG':
        r['live_qty']=5
        r['live_gross']=round(r['live_pts']*MNQ*5,2)
        r['mdl_gross_asat']=round(r['mdl_pts']*MNQ*1,2)
        r['mdl_gross_now']=r['mdl_gross_asat']
        r['cls']='CONFIG_SIZE'
        r['diff_asat']=round(r['live_gross']-r['mdl_gross_asat'],2)
d=pd.DataFrame(rows).sort_values(["date","live_en_t"]).reset_index(drop=True)
d.to_csv("/root/v9/bt/_rec_trades.csv",index=False)
pd.set_option("display.width",300); pd.set_option("display.max_rows",100); pd.set_option("display.max_columns",40)
print(d.to_string())
print()
print("=== by class ===")

mt=d[d.live_qty>0]
print()
print('=== SLIPPAGE (points, live minus model; +entry = paid up, +exit = got more) ===')
for sl,g in mt[mt.en_slip.notna()].groupby('sleeve'):
    print('%-14s n=%2d  entry mean %+.2f median %+.2f | exit mean %+.2f median %+.2f | round-turn cost mean %+.2f pts'
          % (sl,len(g),g.en_slip.mean(),g.en_slip.median(),g.ex_slip.mean(),g.ex_slip.median(),
             (g.en_slip-g.ex_slip).mean()))
g=mt[mt.en_slip.notna()]
print('%-14s n=%2d  entry mean %+.2f median %+.2f | exit mean %+.2f median %+.2f | round-turn cost mean %+.2f pts'
      % ('ALL',len(g),g.en_slip.mean(),g.en_slip.median(),g.ex_slip.mean(),g.ex_slip.median(),(g.en_slip-g.ex_slip).mean()))
print()
print('=== DECOMPOSITION (GROSS $, like-for-like: model AS CONFIGURED LIVE) ===')
base=d[(d.live_qty>0)&(d.cls.isin(['SLIPPAGE','EXIT_DIFF','CONFIG_SIZE']))].mdl_gross_asat.sum()
sl_=d[d.cls=='SLIPPAGE'].diff_asat.sum(); ex_=d[d.cls=='EXIT_DIFF'].diff_asat.sum()
ph_=d[d.cls=='PHANTOM/REGIME'].diff_asat.sum(); cf_=d[d.cls=='CONFIG_SIZE'].diff_asat.sum()
ms_=d[d.cls=='MISSED'].diff_asat.sum(); nl_=d[d.cls=='SLEEVE_NOT_LIVE'].mdl_gross_asat.sum()
print(' model as-at, sleeves that were live   %+10.2f' % base)
print(' + slippage                            %+10.2f' % sl_)
print(' + exit differences                    %+10.2f' % ex_)
print(' + phantom (stale-regime trade)        %+10.2f' % ph_)
print(' + config/size anomaly (06-30 5 lots)  %+10.2f' % cf_)
print(' + missed trades                       %+10.2f' % ms_)
print(' = LIVE GROSS                          %+10.2f' % (base+sl_+ex_+ph_+cf_+ms_))
print(' [R3_RE model-only, sleeve not live]   %+10.2f' % nl_)
print(' model as-at FULL book incl R3_RE      %+10.2f' % (base+nl_))
mn=d.mdl_gross_now.sum()
print(' model TODAY config, full book         %+10.2f' % mn)
rtc=(mt.live_qty.sum())*RT
print(' live contract-RTs %d -> commission $%.2f -> LIVE NET %+.2f' % (mt.live_qty.sum(), rtc, base+sl_+ex_+ph_+cf_+ms_-rtc))
print()
print(d.groupby("cls").agg(n=("cls","size"), live=("live_gross","sum"), mdl_asat=("mdl_gross_asat","sum"), diff=("diff_asat","sum")))

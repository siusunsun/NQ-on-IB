import csv, datetime as dt
from collections import defaultdict

PATH="/root/v9/bt/trades_ORB.csv"
COST=1.54  # points round-trip
MNQ=2.0; NQ=20.0

rows=[]
with open(PATH) as f:
    for r in csv.DictReader(f):
        et_in=dt.datetime.strptime(r["entry_time_et"],"%Y-%m-%d %H:%M")
        et_out=dt.datetime.strptime(r["exit_time_et"],"%Y-%m-%d %H:%M")
        pts=float(r["pnl_points"])
        rows.append(dict(sleeve=r["sleeve"],dir=r["direction"],ein=et_in,eout=et_out,
                         epx=float(r["entry_px"]),xpx=float(r["exit_px"]),
                         reason=r["exit_reason"],pts=pts,reason_r=r["R"]))
rows.sort(key=lambda x:x["ein"])
print(f"total trades={len(rows)}  range {rows[0]['ein'].date()} -> {rows[-1]['ein'].date()}")
print(f"all sleeves == ORB? {all(r['sleeve']=='ORB' for r in rows)}")

def stats(subset, cost=COST):
    n=len(subset)
    if n==0: return None
    net=[r["pts"]-cost for r in subset]
    wins=[x for x in net if x>0]; losses=[x for x in net if x<=0]
    gross_sum=sum(r["pts"] for r in subset)
    net_sum=sum(net)
    winpct=100*len(wins)/n
    aw=sum(wins)/len(wins) if wins else 0
    al=sum(losses)/len(losses) if losses else 0
    gp=sum(wins); gl=-sum(losses)
    pf=gp/gl if gl>0 else float('inf')
    exp=net_sum/n
    # max drawdown on equity curve (points)
    eq=0; peak=0; mdd=0
    for x in net:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return dict(n=n,winpct=winpct,aw=aw,al=al,pf=pf,exp=exp,
               gross_sum=gross_sum,net_sum=net_sum,mdd=mdd,
               nwin=len(wins),nloss=len(losses))

def show(label,st):
    if st is None: print(f"{label}: no trades"); return
    print(f"\n=== {label} ===")
    print(f"  n={st['n']}  win%={st['winpct']:.1f}%  ({st['nwin']}W/{st['nloss']}L)")
    print(f"  avg_win  = {st['aw']:.2f} pt  | MNQ ${st['aw']*MNQ:.0f}  NQ ${st['aw']*NQ:.0f}")
    print(f"  avg_loss = {st['al']:.2f} pt  | MNQ ${st['al']*MNQ:.0f}  NQ ${st['al']*NQ:.0f}")
    print(f"  profit factor = {st['pf']:.3f}")
    print(f"  expectancy/trade = {st['exp']:.3f} pt | MNQ ${st['exp']*MNQ:.2f}  NQ ${st['exp']*NQ:.2f}")
    print(f"  GROSS total = {st['gross_sum']:.1f} pt | MNQ ${st['gross_sum']*MNQ:,.0f}  NQ ${st['gross_sum']*NQ:,.0f}")
    print(f"  NET   total = {st['net_sum']:.1f} pt | MNQ ${st['net_sum']*MNQ:,.0f}  NQ ${st['net_sum']*NQ:,.0f}")
    print(f"  maxDD (net) = {st['mdd']:.1f} pt | MNQ ${st['mdd']*MNQ:,.0f}  NQ ${st['mdd']*NQ:,.0f}")

# Windows
w_start=dt.datetime(2024,7,10); w_end=dt.datetime(2026,7,9,23,59)
optwin=[r for r in rows if w_start<=r["ein"]<=w_end]
full=rows

print("\n########## GROSS vs NET headline (full history) ##########")
g=stats(full,cost=0.0); n=stats(full,cost=COST)
print(f"GROSS: total {g['net_sum']:.1f}pt  MNQ ${g['net_sum']*MNQ:,.0f}  NQ ${g['net_sum']*NQ:,.0f}  PF {g['pf']:.3f}  exp {g['exp']:.3f}pt")
print(f"NET  : total {n['net_sum']:.1f}pt  MNQ ${n['net_sum']*MNQ:,.0f}  NQ ${n['net_sum']*NQ:,.0f}  PF {n['pf']:.3f}  exp {n['exp']:.3f}pt")

show("OPTION-WINDOW 2024-07-10..2026-07-09  (NET of 1.54pt)", stats(optwin))
show("FULL HISTORY  (NET of 1.54pt)", stats(full))

# Half-year
print("\n########## HALF-YEAR (NET of 1.54pt) ##########")
def half(d):
    h=1 if d.month<=6 else 2
    return f"{d.year}-H{h}"
buckets=defaultdict(list)
for r in rows: buckets[half(r["ein"])].append(r)
print(f"{'period':>9} | {'n':>3} | {'win%':>5} | {'PF':>6} | {'netPt':>8} | {'MNQ$':>9} | {'NQ$':>10}")
for k in sorted(buckets):
    st=stats(buckets[k])
    print(f"{k:>9} | {st['n']:>3} | {st['winpct']:>4.0f}% | {st['pf']:>6.2f} | {st['net_sum']:>8.1f} | {st['net_sum']*MNQ:>9,.0f} | {st['net_sum']*NQ:>10,.0f}")

# Year
print("\n########## YEAR-BY-YEAR (NET of 1.54pt) ##########")
ybuckets=defaultdict(list)
for r in rows: ybuckets[r["ein"].year].append(r)
print(f"{'year':>5} | {'n':>3} | {'win%':>5} | {'PF':>6} | {'netPt':>8} | {'MNQ$':>9} | {'NQ$':>10}")
for k in sorted(ybuckets):
    st=stats(ybuckets[k])
    print(f"{k:>5} | {st['n']:>3} | {st['winpct']:>4.0f}% | {st['pf']:>6.2f} | {st['net_sum']:>8.1f} | {st['net_sum']*MNQ:>9,.0f} | {st['net_sum']*NQ:>10,.0f}")

# Decision table (option window)
print("\n########## DECISION TABLE  2024-07-10..2026-07-09 ##########")
ow=stats(optwin)
# option column from opt_report ORB OPTION expression
print(f"{'metric':>18} | {'0DTE option':>14} | {'NQ $20/pt':>14} | {'MNQ $2/pt':>14}")
print(f"{'Total P&L':>18} | {'$28,783':>14} | {'$'+format(ow['net_sum']*NQ,',.0f'):>14} | {'$'+format(ow['net_sum']*MNQ,',.0f'):>14}")
print(f"{'Profit factor':>18} | {'1.22':>14} | {ow['pf']:>14.3f} | {ow['pf']:>14.3f}")
print(f"{'Win %':>18} | {'31.9%':>14} | {format(ow['winpct'],'.1f')+'%':>14} | {format(ow['winpct'],'.1f')+'%':>14}")
print(f"{'Expectancy/trade':>18} | {'$57.7':>14} | {'$'+format(ow['exp']*NQ,',.2f'):>14} | {'$'+format(ow['exp']*MNQ,',.2f'):>14}")
print(f"{'maxDD':>18} | {'$-12,169':>14} | {'$'+format(ow['mdd']*NQ,',.0f'):>14} | {'$'+format(ow['mdd']*MNQ,',.0f'):>14}")
print(f"{'n (signals)':>18} | {'499':>14} | {ow['n']:>14} | {ow['n']:>14}")

# Sanity: last 10 trades
print("\n########## LAST 10 NQ-FUTURES ORB TRADES ##########")
for r in rows[-10:]:
    flag = "  <<< EXIT DATE != ENTRY DATE" if r["ein"].date()!=r["eout"].date() else ""
    print(f"  {r['ein']} -> {r['eout']}  {r['dir']:>5}  ent={r['epx']:.2f} exit={r['xpx']:.2f}  {r['reason']:<8} pts={r['pts']:.2f}{flag}")

# Check all trades for boundary bleed
bleed=[r for r in rows if r["ein"].date()!=r["eout"].date()]
print(f"\nTotal trades with exit date != entry date: {len(bleed)}")
for r in bleed[:20]:
    print(f"  {r['ein']} -> {r['eout']}  {r['reason']}")

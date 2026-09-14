"""_hist_books.py - full-history per-sleeve stats + the two candidate books. READ-ONLY."""
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, pytz
ET = pytz.timezone("America/New_York")
B = "/root/v9/bt/"
SLEEVES = ["VWAP_LONG_R3", "VWAP_SHORT_R1", "TLB_HYB40", "TLB_PIVOT", "R3_RE"]

D = {}
for s in SLEEVES:
    d = pd.read_csv(B + "_hist_tr_%s.csv" % s, parse_dates=["entry_utc", "exit_utc"])
    d["date"] = pd.to_datetime(d["entry_utc"], utc=True).dt.tz_convert(ET).dt.date
    d["year"] = pd.to_datetime(d["entry_utc"], utc=True).dt.tz_convert(ET).dt.year
    d["hold_min"] = (pd.to_datetime(d["exit_utc"], utc=True) - pd.to_datetime(d["entry_utc"], utc=True)).dt.total_seconds() / 60
    D[s] = d

print("=" * 100)
print("HOLD-TIME AUDIT (roll-gap exposure): does any sleeve hold across the 17:00 ET break / a roll?")
print("=" * 100)
for s in SLEEVES:
    d = D[s]
    et_e = pd.to_datetime(d["entry_utc"], utc=True).dt.tz_convert(ET)
    et_x = pd.to_datetime(d["exit_utc"], utc=True).dt.tz_convert(ET)
    cross = (et_e.dt.date != et_x.dt.date).sum()
    print("  %-14s n=%4d  max_hold=%7.0f min  median=%5.0f min  entry/exit on different ET dates: %d"
          % (s, len(d), d["hold_min"].max(), d["hold_min"].median(), cross))

def daily_series(d, mult=1.0):
    g = d.groupby("date")["pnl_usd"].sum() * mult
    g.index = pd.to_datetime(list(g.index))
    return g

def stats_from_trades(d):
    u = d["pnl_usd"].values
    gp = u[u > 0].sum(); gl = -u[u < 0].sum()
    cum = np.cumsum(u); mdd = (cum - np.maximum.accumulate(cum)).min() if len(cum) else 0.0
    ds = daily_series(d)
    sh = ds.mean() / ds.std() * np.sqrt(252) if ds.std() > 0 else 0.0
    return dict(n=len(d), net=u.sum(), exp=u.mean() if len(u) else 0, pf=(gp / gl if gl > 0 else np.inf),
                win=100 * (u > 0).mean() if len(u) else 0, mdd=mdd, sharpe_td=sh,
                trad_days=len(ds))

print("\n" + "=" * 100)
print("B. PER-SLEEVE FULL-HISTORY STATS  (1 MNQ = $2/pt)")
print("=" * 100)
print("%-14s %5s %10s %9s %7s %6s %11s %8s   %s" % ("sleeve", "n", "net$", "exp$/tr", "PF", "win%", "maxDD$", "Sharpe*", "span"))
for s in SLEEVES:
    d = D[s]; st = stats_from_trades(d)
    print("%-14s %5d %10s %9.1f %7.3f %6.1f %11s %8.2f   %s -> %s"
          % (s, st["n"], "{:,.0f}".format(st["net"]), st["exp"], st["pf"], st["win"],
             "{:,.0f}".format(st["mdd"]), st["sharpe_td"], d["date"].min(), d["date"].max()))
print("  *Sharpe = annualized on trading-days-with-a-trade for that sleeve (not comparable across sleeves; book Sharpe below is the real one)")

print("\nPER-CALENDAR-YEAR net$ (1 MNQ)")
yrs = list(range(2021, 2027))
print("%-14s %s" % ("sleeve", "".join("%12d" % y for y in yrs)))
for s in SLEEVES:
    d = D[s]
    row = []
    for y in yrs:
        sub = d[d["year"] == y]
        row.append("%12s" % ("{:,.0f}".format(sub["pnl_usd"].sum()) if len(sub) else "-"))
    print("%-14s %s" % (s, "".join(row)))
print("\nPER-CALENDAR-YEAR n trades")
for s in SLEEVES:
    d = D[s]
    print("%-14s %s" % (s, "".join("%12d" % (d["year"] == y).sum() for y in yrs)))

# ---------------- BOOKS ----------------
BOOKS = {
    "CURRENT":     [("VWAP_LONG_R3", 2), ("VWAP_SHORT_R1", 2), ("TLB_HYB40", 1)],
    "RECOMMENDED": [("VWAP_LONG_R3", 2), ("VWAP_SHORT_R1", 2), ("R3_RE", 1)],
}

def book_daily(spec):
    parts = [daily_series(D[s], q) for s, q in spec]
    idx = sorted(set().union(*[set(p.index) for p in parts]))
    out = pd.Series(0.0, index=pd.DatetimeIndex(idx))
    for p in parts:
        out = out.add(p.reindex(out.index).fillna(0.0), fill_value=0.0)
    return out.sort_index()

def calendar_grid(ser):
    """expand to every weekday between first and last trade so flat days count"""
    full = pd.bdate_range(ser.index.min(), ser.index.max())
    return ser.reindex(full).fillna(0.0)

def bstats(ser):
    r = calendar_grid(ser)
    cum = r.cumsum(); peak = cum.cummax(); dd = cum - peak
    mdd = dd.min()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    neg = r[r < 0]
    sortino = r.mean() / neg.std() * np.sqrt(252) if len(neg) and neg.std() > 0 else np.nan
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    # longest flat = longest stretch (calendar days) without a new equity high
    underwater = (dd < -1e-9).astype(int)
    longest = 0; cur0 = None
    for t, u in underwater.items():
        if u:
            if cur0 is None: cur0 = t
            longest = max(longest, (t - cur0).days)
        else:
            cur0 = None
    return dict(net=r.sum(), yrs=yrs, ann=r.sum() / yrs, sharpe=sharpe, sortino=sortino,
                pf=gp / gl if gl > 0 else np.inf, mdd=mdd, retdd=(r.sum() / abs(mdd)) if mdd < 0 else np.nan,
                flat_days=longest, days=len(r))

print("\n" + "=" * 100)
print("C. BOOKS AT LIVE SIZES  (CURRENT = R3x2 + SHORTx2 + TLB_HYB40x1 ; RECOMMENDED = R3x2 + SHORTx2 + R3_REx1)")
print("=" * 100)
series = {}
for name, spec in BOOKS.items():
    ser = book_daily(spec); series[name] = ser
    st = bstats(ser)
    print("\n### %s   (%s)" % (name, " + ".join("%s x%d" % (s, q) for s, q in spec)))
    print("  span %s -> %s  (%.2f yrs, %d weekdays)" % (ser.index.min().date(), ser.index.max().date(), st["yrs"], st["days"]))
    print("  net=${:,.0f}   annualized=${:,.0f}/yr   Sharpe={:.2f}   Sortino={:.2f}   PF={:.3f}".format(
        st["net"], st["ann"], st["sharpe"], st["sortino"], st["pf"]))
    print("  maxDD=${:,.0f}   ret/DD={:.2f}   longest flat (days under water)={}".format(
        st["mdd"], st["retdd"], st["flat_days"]))

print("\n" + "-" * 100)
print("PER-YEAR:  net$ / Sharpe / maxDD$")
print("-" * 100)
print("%-14s %s" % ("book", "".join("%26d" % y for y in yrs)))
for name in BOOKS:
    r = calendar_grid(series[name])
    cells = []
    for y in yrs:
        ry = r[r.index.year == y]
        if len(ry) == 0 or ry.abs().sum() == 0:
            cells.append("%26s" % "-"); continue
        cum = ry.cumsum(); mdd = (cum - cum.cummax()).min()
        sh = ry.mean() / ry.std() * np.sqrt(252) if ry.std() > 0 else 0
        cells.append("%26s" % ("{:>9,.0f} {:>5.2f} {:>9,.0f}".format(ry.sum(), sh, mdd)))
    print("%-14s %s" % (name, "".join(cells)))

print("\n" + "-" * 100)
print("MONTHLY STATS")
print("-" * 100)
for name in BOOKS:
    r = calendar_grid(series[name])
    m = r.resample("ME").sum()
    m = m[m.index >= pd.Timestamp(r.index.min())]
    tot = m.sum()
    top1 = m.max(); top3 = m.nlargest(3).sum()
    print("\n### %s  (%d months)" % (name, len(m)))
    print("  mean=${:,.0f}  MEDIAN=${:,.0f}  %%positive={:.1f}%%  std=${:,.0f}".format(m.mean(), m.median(), 100 * (m > 0).mean(), m.std()).replace("%%", "%"))
    print("  worst=${:,.0f} ({})   best=${:,.0f} ({})".format(m.min(), m.idxmin().strftime("%Y-%m"), m.max(), m.idxmax().strftime("%Y-%m")))
    print("  best month = {:.1f}% of total profit;  top-3 months = {:.1f}% of total profit".format(100 * top1 / tot, 100 * top3 / tot))
    print("  worst 5 months: " + ", ".join("{} ${:,.0f}".format(i.strftime("%Y-%m"), v) for i, v in m.nsmallest(5).items()))
    print("  best  5 months: " + ", ".join("{} ${:,.0f}".format(i.strftime("%Y-%m"), v) for i, v in m.nlargest(5).items()))

# ---------------- comparison vs the 2-yr window ----------------
print("\n" + "=" * 100)
print("D4. LONG SAMPLE vs THE OLD 2-YR WINDOW (2024-08-01 -> 2026-08-26)")
print("=" * 100)
for name in BOOKS:
    r = calendar_grid(series[name])
    for lbl, sub in [("FULL 2021-08..2026-08", r),
                     ("2yr 2024-08..2026-08", r[r.index >= "2024-08-01"]),
                     ("EARLY 2021-08..2024-07", r[r.index < "2024-08-01"])]:
        if len(sub) == 0: continue
        yrs2 = (sub.index[-1] - sub.index[0]).days / 365.25
        cum = sub.cumsum(); mdd = (cum - cum.cummax()).min()
        sh = sub.mean() / sub.std() * np.sqrt(252) if sub.std() > 0 else 0
        mm = sub.resample("ME").sum()
        print("  %-12s %-24s net=$%9s  ann=$%8s  Sharpe=%5.2f  maxDD=$%9s  monthly mean=$%6.0f med=$%6.0f  %%pos=%4.1f%%"
              % (name, lbl, "{:,.0f}".format(sub.sum()), "{:,.0f}".format(sub.sum() / yrs2), sh,
                 "{:,.0f}".format(mdd), mm.mean(), mm.median(), 100 * (mm > 0).mean()))

# ---------------- emit combined daily ----------------
allidx = calendar_grid(series["CURRENT"]).index.union(calendar_grid(series["RECOMMENDED"]).index)
out = pd.DataFrame({
    "current_usd": calendar_grid(series["CURRENT"]).reindex(allidx).fillna(0.0),
    "recommended_usd": calendar_grid(series["RECOMMENDED"]).reindex(allidx).fillna(0.0),
})
out.index.name = "date"
out.to_csv(B + "_hist_books.csv", float_format="%.2f")
print("\nwrote %s_hist_books.csv rows=%d  current=$%s recommended=$%s"
      % (B, len(out), "{:,.0f}".format(out.current_usd.sum()), "{:,.0f}".format(out.recommended_usd.sum())))

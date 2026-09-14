"""dash_base_build.py - ONE-TIME (re-runnable) builder for the frozen dashboard base.

Produces two artefacts consumed by the daily job:

  /root/v9/bt/dash_monthly_base.json  - frozen monthly % table through the end of
                                        the last COMPLETED month, plus the running
                                        equity at the end of every frozen month.
  /root/v9/bt/dash_daily_closes.csv   - ET-date -> daily close over the FULL archive,
                                        so the daily job can compute the 200-day SMA
                                        regime without ever loading the full 1-min set.

The monthly numbers come from the already-validated full-history sleeve outputs
(_hist_tr_*.csv + _r3_48_daily.csv), using exactly the arithmetic of _cagr.py:
capital $22,500, fixed 5-MNQ sizing, monthly % of RUNNING equity, $2.04/RT cost.

Only step (2) touches the full archive, and it does so in 200k-row chunks
(close column only), so peak RSS stays small.  Run manually, serially.
"""
import sys, os, json
sys.path.insert(0, "/root/v9/bt")
import numpy as np
import pandas as pd
import dash_lib as L

FREEZE_THROUGH = os.environ.get("FREEZE_THROUGH", "")   # e.g. 2026-07; blank = auto


def sleeve_daily(tag, qty):
    d = pd.read_csv(L.BT + "_hist_tr_%s.csv" % tag)
    col = [c for c in d.columns
           if "entry" in c.lower() and any(k in c.lower() for k in ("date", "utc", "time"))][0]
    t = pd.to_datetime(d[col], utc=True, errors="coerce")
    if t.isna().all():
        t = pd.to_datetime(d[col], errors="coerce").dt.tz_localize("UTC")
    d["dt"] = t.dt.tz_convert(L.ET).dt.date
    pc = [c for c in d.columns if c.lower() in ("pnl_usd", "pnl_usd_1mnq", "net_usd", "usd")][0]
    d["net"] = d[pc] * qty - L.RT * qty
    return d.groupby("dt")["net"].sum()


def build_monthly():
    VL = sleeve_daily("VWAP_LONG_R3", L.QTY["VWAP_LONG_R3"])
    VS = sleeve_daily("VWAP_SHORT_R1", L.QTY["VWAP_SHORT_R1"])
    TL = sleeve_daily("TLB_HYB40", L.QTY["TLB_HYB40"])
    r = pd.read_csv(L.BT + "_r3_48_daily.csv")          # already NET of cost, 1 MNQ
    r["dt"] = pd.to_datetime(r["date"]).dt.date
    R3 = r.set_index("dt")["usd"]

    parts = [VL, VS, TL, R3]
    start = min(x.index.min() for x in parts)
    end = max(x.index.max() for x in parts)
    I = sorted(set(pd.bdate_range(start, end).date) | set().union(*[set(x.index) for x in parts]))
    I = pd.Index([d for d in I if start <= d <= end])
    al = lambda s: s.reindex(I).fillna(0.0)
    B = al(VL) + al(VS) + al(TL) + al(R3)

    mo = pd.Series(pd.to_datetime(I).to_period("M").astype(str), index=I)
    m = B.groupby(mo).sum()

    eq = L.CAP
    months = {}
    for k in sorted(m.index):
        v = float(m[k])
        months[k] = dict(pnl=round(v, 2), pct=round(100.0 * v / eq, 6),
                         equityStart=round(eq, 2), equityEnd=round(eq + v, 2))
        eq += v
    return months, str(end)


def main():
    L.log("[base] START  memAvail=%.0fMB" % L.mem_available_mb())
    if L.mem_available_mb() < L.MIN_FREE_MB:
        L.log("[base] ABORT: low memory")
        return 2

    # ---- (1) frozen monthly table -------------------------------------
    months, last_date = build_monthly()
    all_m = sorted(months)
    if FREEZE_THROUGH:
        freeze = FREEZE_THROUGH
    else:
        freeze = str(pd.Period(pd.Timestamp(last_date), freq="M") - 1)
    frozen = {k: v for k, v in months.items() if k <= freeze}
    lastk = max(frozen)
    L.log("[base] months %s .. %s   frozen through %s  equityEnd=$%.2f"
          % (all_m[0], all_m[-1], lastk, frozen[lastk]["equityEnd"]))

    # ---- (2) full-archive daily closes (chunked) -----------------------
    L.log("[base] scanning full archive for daily closes ...")
    s = L.build_daily_closes()
    L.log("[base] daily closes n=%d  %s .. %s  peakRSS=%.0fMB"
          % (len(s), s.index[0].date(), s.index[-1].date(), L.rss_mb()))

    out = dict(
        generated=pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        capital=L.CAP,
        basis="THEORETICAL (backtest signals, not live fills) - MNQ $2/pt at deployed size",
        book={"VWAP_LONG_R3": 2, "VWAP_SHORT_R1": 1, "TLB_HYB40": 1, "R3_RE": 1},
        costPerRoundTurn=L.RT,
        lastFrozenMonth=lastk,
        equityAfterFrozen=frozen[lastk]["equityEnd"],
        months=frozen,
    )
    tmp = L.BASE_JSON + ".tmp"
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, L.BASE_JSON)
    L.log("[base] wrote %s" % L.BASE_JSON)

    # ---- (3) echo the table for eyeball validation ----------------------
    hdr = "YEAR " + "".join("%8s" % x for x in
                            ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL",
                             "AUG", "SEP", "OCT", "NOV", "DEC"]) + "%9s" % "YTD"
    print(hdr)
    years = sorted(set(k[:4] for k in frozen))
    for y in years:
        cells = ""
        ytd = 1.0
        for i in range(1, 13):
            k = "%s-%02d" % (y, i)
            if k in frozen:
                p = frozen[k]["pct"]
                cells += "%+8.1f" % p
                ytd *= (1 + p / 100.0)
            else:
                cells += "%8s" % "-"
        print("%s %s%+8.1f%%" % (y, cells, 100 * (ytd - 1)))
    print("peak RSS %.0f MB" % L.rss_mb())
    L.log("[base] DONE peakRSS=%.0fMB" % L.rss_mb())
    return 0


if __name__ == "__main__":
    sys.exit(main())

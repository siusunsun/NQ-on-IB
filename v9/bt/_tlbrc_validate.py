"""_tlbrc_validate.py — REFERENCE backtest for the TLB-RECLAIM forward-tag logger.

NOTE (2026-08-27): a file of this name was requested as a pre-existing "validated" reference
but did NOT exist on the VPS (verified). This is the reference, freshly built. It imports the
SAME core (tlbrc_spec.run_engine) that the live logger uses, so live == backtest BY
CONSTRUCTION. Read-only research over the shared NQ 1-min archive. Writes _tlbrc_fires.csv
next to itself for the live parity self-test to compare against.

Strategy + reconstruction assumptions: see /root/tlbrc_paper/tlbrc_spec.py header.
"""
import sys, glob
import numpy as np, pandas as pd
sys.path.insert(0, '/root/tlbrc_paper')
import tlbrc_spec

NQ_DIR = "/root/v9/data_1m/NQ"
OUT = "/root/v9/bt/_tlbrc_fires.csv"


def load_all():
    fs = sorted(glob.glob(NQ_DIR + "/*.csv"))
    dfs = []
    for f in fs:
        d = pd.read_csv(f); d.columns = [c.strip().lower() for c in d.columns]
        tc = next(c for c in ('time', 'timestamp', 'date', 'datetime') if c in d.columns)
        d[tc] = pd.to_datetime(d[tc], utc=True, errors='coerce')
        d = d.dropna(subset=[tc]).set_index(tc)
        if 'volume' not in d.columns:
            d['volume'] = 0.0
        dfs.append(d[['open', 'high', 'low', 'close', 'volume']].astype(float))
    df = pd.concat(dfs).sort_index()
    return df[~df.index.duplicated(keep='last')].sort_index()


if __name__ == "__main__":
    df = load_all()
    bars = tlbrc_spec.resample_15m(df)
    bars = bars[~bars.index.duplicated(keep='last')].sort_index()
    print(f"1-min bars {len(df):,}  ->  15-min bars {len(bars):,}  "
          f"({bars.index[0]} .. {bars.index[-1]})")
    fires = tlbrc_spec.run_engine(bars)
    closed = [f for f in fires if f['exit_reason'] != 'OPEN']
    openf = [f for f in fires if f['exit_reason'] == 'OPEN']
    t = pd.DataFrame(fires)
    cols = ['fire_time', 'entry', 'stop', 'risk', 'exit_time', 'exit', 'exit_reason', 'R', 'net_usd']
    t.to_csv(OUT, index=False, columns=cols)
    yrs = pd.to_datetime(t['fire_time']).dt.year
    span_yrs = max(1e-9, (bars.index[-1] - bars.index[0]).days / 365.25)
    print(f"reclaim fires: total={len(fires)}  closed={len(closed)}  open={len(openf)}  "
          f"(~{len(fires)/span_yrs:.1f}/yr over {span_yrs:.2f}y)")
    if closed:
        cdf = pd.DataFrame(closed)
        R = cdf['R'].astype(float); net = cdf['net_usd'].astype(float)
        win = (R > 0).mean() * 100
        gp = net[net > 0].sum(); gl = -net[net < 0].sum()
        pf = gp / gl if gl > 0 else float('inf')
        print(f"closed: win%={win:.1f}  sumR={R.sum():+.2f}  net=${net.sum():+,.0f}  PF={pf:.2f}")
        print(f"per-year fires: {dict(yrs.value_counts().sort_index())}")
    print(f"wrote {OUT}")
    print("\nlast 8 reclaim fires:")
    show = t.tail(8)[cols].copy()
    print(show.to_string(index=False))

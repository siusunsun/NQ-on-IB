# Backtest summary

Independent implementation from REPRO_SPEC.md and the two supplied data files only.

| Sleeve | Trades | Total points | Win rate |
|---|---:|---:|---:|
| VWAP_LONG_R3 | 342 | 3,342.25000000 | 33.0409% |
| VWAP_SHORT_R1 | 222 | 836.50000000 | 54.9550% |
| TLB_HYB40 | 672 | 4,270.00000000 | 55.9524% |
| R3_RE_mw48 | 161 | 2,249.08495606 | 62.7329% |

Runtime: 14.310 seconds (Python 3.14.4, pandas 3.0.2, NumPy 2.4.4).

Run: `python repro/backtest.py`. Synthetic checks: `python repro/test_backtest.py`.

## Input and validation

- 1-minute rows: 1,990,457; range 2020-12-30T10:04:00Z to 2026-08-26T03:59:00Z.
- Retained 5-minute bars: 398,182; dropped final bucket: 2026-08-26T03:55:00Z.
- Observed ET daily closes: 1,761; defined regime dates: 1,556 (2021-08-27 to 2026-08-25).
- Input order, uniqueness, finite/nonmissing source values and minute alignment checked; CSV columns/counts, P&L arithmetic, finite output prices, VWAP caps and base-sleeve busy constraints validated after writing.

## Diagnostics and potentially surprising behavior

- VWAP_LONG_R3: 798 detector signals; 342 taken before VWAP cap; 0 otherwise-eligible signals blocked while busy.
  Discarded for risk > 200: 0; their simulated P&L (excluded from results): 0.00000000 points; otherwise-eligible signals blocked by those trades: 0.
- VWAP_SHORT_R1: 2,056 detector signals; 222 taken before VWAP cap; 3 otherwise-eligible signals blocked while busy.
  Discarded for risk > 200: 0; their simulated P&L (excluded from results): 0.00000000 points; otherwise-eligible signals blocked by those trades: 0.
- TLB_HYB40: 17,874 detector signals; 672 taken before VWAP cap; 190 otherwise-eligible signals blocked while busy.
- R3: 224 kept stopped LONG parents; 156 VWAP-kind entries; 5 UPPER-kind entries; 30 parents reached the 48-bar limit; 0 missing parent buckets; 0 breakouts rejected for nonpositive risk.
- VWAP_LONG_R3 exit reasons: GAP_FLATTEN=23, STOP=224, TARGET=95.
- VWAP_SHORT_R1 exit reasons: GAP_FLATTEN=1, STOP=99, TARGET=122.
- TLB_HYB40 exit reasons: EOD=244, STOP=198, TARGET=230.
- R3_RE_mw48 exit reasons: FLATTEN=7, STOP=56, TARGET=98.
- Exit-before-entry timestamps from literal terminal/session rules: 0.
- R3 trades may overlap each other and base trades; they have no independent regime, holiday, wide-stop cap or busy filter. No costs or position sizes are applied.

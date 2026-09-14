# Red team the idea-test harness BEFORE the holdout is opened

You proposed the ideas in `../ideas/out/IDEAS.md`. Another team built the test harness; you did
not grade your own ideas. Your job now: find anything in the harness or the development-stage
inference that makes the results wrong — in either direction (a real edge hidden, or a fake one
shown). You succeed by finding real problems; confirm every claim by running code.

## Files
- `PREREG.md` — the protocol, written before any candidate was simulated. Judge the code against it
  and against your own rule text in IDEAS.md.
- `sim.py` — portfolio simulator with management hooks. It imports your verified engine
  `../repro/repro/backtest.py` for detectors/regime. `gate0.py` proves that with no rule active it
  reproduces `../repro/out/trades_*.csv` exactly.
- `audit.py` — independent re-derivation of every executed management action.
- `run_dev.py` — runs the 9 variants, bootstrap, Holm, matched random-trigger nulls; writes
  `out/DEV_REPORT.md` and `out/dev_results.json` (development period only).
- `out/trades_*.csv` contain the whole period. **Do not compute or report anything dated on or after
  2025-08-27** — that is the sealed holdout. `run_holdout.py` exists but must NOT be run.
- Data: `../repro/data/`.

## Check at minimum
1. Does each rule in `sim.py` implement IDEAS.md exactly (thresholds, eligibility, timing, fills,
   what counts as a post-entry bar, MFE, target-type lock, 1-min lookback gaps, C5 equity/reserve
   arithmetic and processing order)? Any look-ahead?
2. Re-entry linkage: a MANAGEMENT_EXIT or MGMT_CUT must never create a re-entry right; a later real
   STOP on the remaining lot must. Busy state must be released at the earlier exit.
3. Costs: $5.40 per contract per round turn, charged once per contract on its exit leg, 1x/2x/3x.
4. The statistics in `run_dev.py`: stationary bootstrap, Holm, drop-top-5, fold construction,
   drawdown on the daily realised curve, and whether the matched null is a fair null for each rule
   (C1-C4, C6, C8 random exits of losing positions at the same age/clock; C9 random skips).
5. Whether any candidate's verdict would flip under a defensible correction you can demonstrate.

## Output — `out/CODEX_REDTEAM.md`
For each finding: file:line, what is wrong, a concrete demonstration (command + numbers, dev period
only), severity (changes a verdict / changes a number / cosmetic), and the fix. Say explicitly
which verdicts, if any, you believe are wrong. Do not edit any file except that output; put scratch
scripts under `work/`.

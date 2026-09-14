# Pre-registration — backtest of Codex's NQ ideas (IDEAS.md, 2026-09-14)

Written 2026-09-14 BEFORE any candidate was simulated. Nothing below may be changed after results
are seen; any change is a new, counted variant and must be logged in the addendum at the bottom.

## Why this protocol
The red team (redteam/out/REDTEAM.md) showed the R3 re-entry evidence was weakened by in-sample
selection, an independence bootstrap, and a few days carrying the edge. Fixes applied here:
every variant is counted up front; no parameter is tuned anywhere (Codex fixed every threshold
with no data); a dependence-preserving day-block bootstrap; family-wise correction; day-level
concentration; a sealed holdout for the deploy decision.

## Engine and control
- Engine: Codex's independent re-implementation `repro/repro/backtest.py` (matches our harness
  1,236/1,236 base trades + 161 R3 re-entries). The test harness `ideatest/sim.py` reuses its
  detectors and regime table unchanged and re-implements the portfolio loop with management hooks.
- **Gate 0:** with no rule active, `sim.py` must reproduce `repro/out/trades_*.csv` exactly
  (times, prices, reasons, counts) before any candidate is run.
- Book: VWAP_LONG_R3 x2, VWAP_SHORT_R1 x1, TLB_HYB40 x1, R3 re-entry (mw48) x1 MNQ, $2/pt.
- Cost: **$5.40 per contract round-turn**, all-in (it already contains the measured 1.88pt
  slippage: 1.88 x $2 + $1.22 = $4.98 <= $5.40). Nothing is added on top. Stress 1x/2x/3x on the
  whole book, candidate and control alike; rules that reference c keep c/v = 2.7pt fixed.
- The entire portfolio path is recomputed per candidate: earlier exits free a sleeve (busy state),
  a management exit is labelled MANAGEMENT_EXIT and creates **no** R3 re-entry right; an original
  stop fill keeps it. Lost/changed re-entry P&L is part of the comparison.

## Execution conventions (fixed now)
- Decisions at a completed 5-min close (a bucket is complete when the clock passes its end) or,
  for #6, a completed 1-min close. Fill = **open of the next available 1-min bar**, never the
  decision close. If that open gaps through the original stop or target, the original order is
  deemed to fill first (booked exactly as the control books it) and the added exit is cancelled.
- MFE = max favourable excursion in points from the entry minute (fully post-entry: entries
  occur at a bucket boundary) through the latest completed bar. u = s(C-E)/R, R frozen at entry.
- Post-entry 5-min bars are counted as completed buckets from the entry bucket onward.
- 1-min lookbacks (#6) never span a gap > 30 minutes (keeps them off the daily halt, hence off
  the 18:00 ET roll boundary). #8's 60-increment sigma uses the latest 60 rows.
- Daily P&L is booked on the ET date of each exit leg; drawdown on that daily realised curve.

## Period split (fixed now)
- Development: ET dates 2021-08-27 .. 2025-08-26 — 8 complete 6-month folds starting 2021-08-27.
- **Holdout (sealed): 2025-08-27 .. 2026-08-25** — 2 folds. Not printed, plotted or inspected
  until the development verdict is written to `out/DEV_VERDICT.md`. Holdout is then reported for
  all candidates, but only development passers are eligible for deployment.
- No training window is needed: no rule has a fitted parameter (Codex's own contract: "the
  training window supplies only prescribed historical state"). All dev folds are evaluated.

## The family — 9 counted variants (no others will be run without an addendum)
| id | Codex # | rule (exact parameters from IDEAS.md) | objective |
|---|---|---|---|
| C1 | 1 | R3 parent: from post-entry bar 15, MFE < 0.25R and u <= -0.50 -> exit all | Risk |
| C2 | 2 | R3 parent + VWAP short: bar >= 6, same UTC session, s(Vt-Vt-6) <= -0.25R, s(C-Vt) < 0, u <= -0.25 -> exit | Profit |
| C3 | 3 | TLB: after 2 post-entry bars, last 2 closes < L(j)-0.25R (entry pivots, frozen) and MFE < 0.50R -> exit | Profit |
| C4 | 4 | R3 re-entry: u <= 0 and G <= 0.5D + 2.7pt on 2 consecutive 5-min checks (target type locked) -> exit | Profit |
| C5 | 5 | Gross stop-risk ceiling: admit largest qty with total reserved risk <= 1% of marked equity ($22,500 base); order R3, TLB, short, re-entry | Risk |
| C6 | 6 | 2-lot R3 parent, losing: Qrecent(10) >= 4 x Qprior(60) downside semivariance -> sell 1 lot, once | Risk |
| C8 | 8 | R3 parent + short, 5-min closes 15:25..<15:55 ET: losing and adverse > 2 sigma60 sqrt(m) -> exit | Risk |
| C9 | 9 | TLB + short: skip signal if 2 x Rplan < 5 x 5.40 (Rplan < 13.5pt) | Profit |
| B1 | (5,6 benchmark) | R3 parent always 1 lot | Risk |

Deferred, NOT tested (no data), still counted if ever run: #7 (needs point-in-time CPI/FOMC
schedule), #10 (needs MNQ quotes — untestable), #11 (needs ES 1-min).

## Pass criteria — development stage (all must hold)
**Profit objective (C2, C3, C4, C9)**
- P1 incremental net (candidate - control) > 0 at 1x AND 2x cost.
- P2 increment > 0 in >= 5 of the 8 dev folds (1x).
- P3 increment still > 0 after deleting the 5 ET dates with the largest positive daily increment.
- P4 stationary day-block bootstrap (mean block 10 trading days, 5,000 reps, paired, all ET
  trading days incl. zero days): p = P*(mean increment <= 0); **Holm-adjusted across the 9 at
  FWER 0.10**.
- P5 null: the increment ranks >= 90th percentile of 200 matched random-trigger reps (below).
- P6 3x cost disclosed; an increment < 0 at 3x marks it cost-fragile = not deployable.

**Risk objective (C1, C5, C6, C8, B1)**
- R1 stitched dev max drawdown lower than control at 1x AND 2x.
- R2 net >= 95% of control net at 1x AND 2x.
- R3 fold max-DD strictly lower in >= 5 of 8 folds (ties are not improvements).
- R4 after deleting the 5 ET dates with the largest positive daily increment, R1 and R2 still hold.
- R5 paired block bootstrap: p = P*(candidate DD >= control DD); Holm-adjusted with the family at 0.10.
- R6 null: candidate net/maxDD >= 90th percentile of 200 matched random reps (C1, C6, C8);
  C5 and C6 must also beat B1 on net/maxDD (else it is plain deleveraging).
- 3x disclosed as P6.

**Matched random-trigger null (200 reps each)** — tests whether the trigger carries information
beyond cutting a random losing position at the same point. For every executed activation, draw
(without replacement within a rep) a control position of the same sleeve that is open and losing
(u < 0) at the same decision point — same post-entry 5-min age (C1, C2, C3, C4), same 1-min age and
2 lots held (C6), same ET clock bucket (C8) — and apply the same action there. If no candidate
exists at the exact age, widen by +-1 bucket up to +-5. C9: skip the same number of taken signals
per sleeve at random. Every rep re-simulates the whole book.

## Holdout stage (deployment gate, only for dev passers)
Same direction on the holdout at 1x AND 2x: Profit — increment > 0; Risk — DD lower and net
retention >= 95%. Nothing is re-tuned between the stages. A candidate passing both stages is a
candidate for paper forward testing first, not straight to live.

## Reporting
Every candidate is reported, pass or fail, with: activations, increment by fold, 1x/2x/3x,
drop-top-5, bootstrap p (raw and Holm), null percentile, and the decomposition of saved losses,
forgone recoveries and forgone R3 re-entry P&L for management rules.

## Addendum (changes after this point, with reason and date)
**2026-09-14, after the dev verdict, before the holdout — Codex harness red team (out/CODEX_REDTEAM.md).**
- Fixed in sim.py (gate 0 + audit re-pass; C2 / C6 / C9 dev increments identical to the cent):
  terminal leg = last appended leg (cut + stop in the same minute mislabelled STOP as MGMT_CUT,
  which suppressed re-entry search; 3 C6 cases, no re-entry found, P&L unchanged); a pending VWAP
  management order is cancelled at the 15:55 mandatory flatten (0 real cases).
- Known, NOT fixed (none changes a verdict; fix before reusing the harness): C5 sizing uses 1x-cost
  equity when stressed (2x net is -$1,012, not +$2,185); C5 settles same-minute fills at admission;
  C6 null sometimes executes 27-28 cuts vs 29 (repaired: pct 56.5, still fails); null RNG streams
  shared across periods (dev-only percentiles C2 97, C9 96.5); signals computed over the whole file.
- Correction to DEV_VERDICT diagnostics: the C1 counts quoted there (125 at bar 15, 4 <= -0.5R,
  median R 18.5) were full-period counts and so touched holdout-period trades (counts only, no P&L).
  Dev-only: 82 at bar 15, 1 <= -0.5R, median R 15.75. C9 worst-10%-days improves +$54.80 (not 0).
- **Material caveat on the two passes.** Under Codex's ORIGINAL contract (IDEAS.md) both fail:
  (a) family-adjusting the matched random null (plus-one p 0.035 / 0.040 -> Holm 0.31 / 0.32);
  (b) keeping only folds after 24 months of history, drop-top-5 turns negative for both
  (C2 -$38, C9 -$18). My PREREG replaced the null's family adjustment with a bootstrap one and
  waived the 24-month window. The frozen verdict stands as "PASS under PREREG", but it is
  weak evidence; it must not be described as a validated edge.
- Holdout opened as registered for C2 and C9 only.

**2026-09-14 — C7 (Codex #7, scheduled CPI/FOMC exposure release) registered BEFORE any C7 run.**
Family becomes 10 counted variants. Objective: **Risk**. Rules exactly as IDEAS.md #7:
- Events (`events/events.csv`, built from primary sources): 67 CPI releases at 08:30 ET (BLS
  archived-release filenames = actual release dates, incl. the shutdown-delayed 2025-10-24 and no
  release in Nov 2025) and 45 FOMC statements at 14:00 ET on the last meeting day (Fed calendar page;
  the 2025-08-22 notation vote excluded — not a scheduled statement). Actual dates are used as the
  point-in-time schedule: every reschedule in 2021-2026 was announced days before the release.
- At the last completed 1-min close at or before e-5 min, any open R3 parent, VWAP short or R3
  re-entry exits at the next 1-min open (same fill/gap/flatten conventions as the other rules;
  label MANAGEMENT_EXIT = no re-entry right). TLB untouched.
- No new R3 parent / VWAP short entry with entry time in [e-5, e+10) (signal discarded, sleeve not
  busy). An R3 re-entry breakout whose 5-min bar overlaps [e-5, e+10) is discarded; the armed
  attempt keeps scanning on its original 48-bar clock (not paused or restarted).
- Criteria: the Risk set R1-R6 + P6 as registered, R5 Holm over the 10 bootstrap p-values.
  Null (R6): 200 reps, every event moved to the same ET clock time on a random non-event weekday
  trading date of the same period (dev / holdout), without replacement; candidate net/maxDD must
  rank >= 90th pct. Disclosed too (Codex finding 8B): the plus-one null p, Holm-adjusted over 10.
- Same dev/holdout split. Caveat: the holdout has been seen for the control and other candidates,
  but no C7 result on any period has been seen. Holdout for C7 is run only if dev passes.

**2026-09-14 — C7 result (dev):** FAIL. 4 dev activations (6 in 5 yrs), 0 entries suspended.
All 4 exited R3 parents were control TARGET fills in the release minute itself (CPI 2021-09-14,
2023-05-10; FOMC 2023-12-13, 2024-09-18): -$677 at every cost level, DD unchanged, null pct 4.5,
Holm(10) 1.0. Holdout not run (dev failed). Holm over 10 leaves C2 0.041 / C9 0.008 unchanged in verdict.
Harness note: pandas 3 datetimes are microsecond-based — `.astype("int64") // 60e9` silently put all
events in 1970 (0 activations); caught by an independent count, fixed with Timedelta division.

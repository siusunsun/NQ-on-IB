# Red team — try to break the evidence behind a live trading decision

A team has put real money behind one conclusion. Your job is to find out whether that conclusion is
**wrong, overstated, or fragile**. You succeed by finding problems, not by agreeing. Be concrete:
every criticism must come with a computation on the supplied data, or a precise argument.

## The claim under attack

"Adding the **R3 re-entry** overlay (`max_wait = 48`) to the existing 3-sleeve NQ book adds real,
robust value: about **+$3,629 net over 5 years for only +$32 of extra max drawdown**, lifting the
book's Sharpe from 1.17 to 1.47. It passes walk-forward, a bootstrap, an era split, 3× costs and a
concentration test." On that basis it was deployed live at 1 MNQ on 2026-09-14.

Secondary claim: the whole book's published headline — **CAGR 12.9%, Sharpe 1.47, max DD 12.9%** of a
$22,500 base — is honest.

## What you have (`data/`)

- `_hist_tr_{VWAP_LONG_R3,VWAP_SHORT_R1,TLB_HYB40}.csv` — every trade of the 3 base sleeves,
  2021-08..2026-08, gross P&L at 1 MNQ (`pnl_usd`, $2/point). Live sizes: VWAP_LONG_R3 ×2, others ×1.
- `_r3re_fires_mw_{none,12,24,48}.csv` — R3 re-entry trades for four `max_wait` settings
  (`yr, gross, dt`; gross $ at 1 MNQ). **48 is the deployed one.**
- `_ra_r3re_sweep.csv` — the **full one-at-a-time sweep** from which the R3 re-entry configuration was
  chosen: 14 configurations over `wait`, `stop_src`, `magnet`, `max_wait`. Note the `net` column there
  used an OLD cost of $2.04/round-turn; the current cost is **$5.40 per contract round-turn**
  (1.88 pts measured slippage × $2 + $1.22 commission), applied as `gross − 5.40 × contracts`.
- `_r3re_analyse.py` + `battery_output.txt` — the team's own test battery and its output. The script
  reads from `/root/v9/bt/`; point it at `data/` to re-run it.
- `codex_ideas_for_reference.md` — an earlier review that criticised, among other things, the
  bootstrap for resampling trades as if independent.

The backtest implementation itself has been independently reproduced trade-for-trade, so assume the
trade lists are a faithful execution of the written rules. Attack the **inference**, not the code.

## Attack surface (at minimum)

1. **Selection bias / multiple testing.** Only `max_wait` was walk-forward validated; `wait`,
   `stop_src`, `magnet` were chosen in-sample from the sweep. How much of +$3,629 is selection?
   Compute a deflated Sharpe or equivalent with an honest trial count, and say what count is honest.
2. **The walk-forward itself.** Is choosing `max_wait` per fold from {None,12,24,48} on training
   P&L meaningful with this few trades per fold? How sensitive is the verdict to fold boundaries and
   window lengths?
3. **The bootstrap and the "+$32 drawdown".** Are R3 re-entry trades independent of the parent
   sleeve (96% share a day with VWAP_LONG_R3)? Build a null that preserves dependence (e.g. block or
   day-level resampling of the combined book, or randomising which parent stop-outs get a re-entry).
   Is the tiny drawdown increase robust, or an artefact of where the few big days fall?
4. **Concentration.** Drop-top-10 leaves about +$69. Is the edge carried by a handful of trades or
   days? Which, and do they share a regime or period?
5. **Regime / era dependence.** Most of the P&L is 2025–2026. Is this a recent-regime effect that
   the era split hides?
6. **Costs.** Is $5.40 per round-turn realistic for a 1-lot MNQ stop-entry at a breakout, where
   slippage may be worse than average? What cost kills it?
7. **The headline figures.** Recompute CAGR, Sharpe and max DD from the trade lists (sizes above,
   $5.40 cost, $22,500 base). Are they right? Is annualising a daily Sharpe over a book that trades on
   a minority of days legitimate as reported?
8. Anything else you find.

## Output — `out/REDTEAM.md`

For each finding: **claim attacked**, **what you computed** (with numbers), **verdict**
(`BREAKS IT` / `WEAKENS IT` / `SURVIVES`), and **what the team should do**. End with an overall
verdict on whether R3 re-entry should stay live, and the single most important thing to fix in their
validation process. Put any scripts you write in `work/`. Use only this folder.

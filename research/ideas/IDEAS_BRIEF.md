# Idea generation — how could this NQ futures book make more money, or lose less?

You are a senior quant researcher brought in for **fresh ideas**. Do NOT run backtests and do NOT
claim results — you have no data here. Your output is a set of **precise, testable hypotheses** that
another team will test with a strict, fixed validation protocol. Quality over quantity: 8–15 ideas.

## The book today (MNQ micro futures, $2/point, live on Interactive Brokers since Jul 2026)

A daily **regime** from the prior close decides which sleeves may open trades:
S200 = close > 200-day SMA; R20 = 20-day return > 0.
BOTH_BULL -> TLB_LONG + VWAP_LONG_R3 · ZONE_A -> VWAP_LONG_R3 · ZONE_B -> TLB_LONG · BOTH_BEAR -> VWAP_SHORT_R1.

All sleeves run on 5-minute bars (session VWAP anchored 00:00 UTC, ±2σ bands):
- **VWAP_LONG_R3** (2 MNQ): 5 consecutive closes below VWAP−2σ, then a close back inside -> long;
  stop = lowest low of the prior 20 bars; target = 3R. Exits intrabar on 1-min bars; flat 15:55 ET.
- **VWAP_SHORT_R1** (1 MNQ): 3 closes above VWAP+2σ, close back inside -> short; 1R target.
  Weak on its own; kept as the only short exposure ("regime insurance").
- **TLB_LONG** (1 MNQ): close breaks above a trend line through the last 2 pivot highs (pivot k=3);
  stop = last pivot low, widened to the 20-bar low if narrower than 40 pts; 1R target; entries only
  10:00–11:55 and 14:55–15:20 ET; flat 15:20 ET.
- **R3 re-entry** (1 MNQ): after a VWAP_LONG_R3 trade is STOPPED out, buy the first break above the
  post-stop high (from 2 bars after the stop, up to 48 bars); stop = the failed move's low; target =
  session VWAP (or the +2σ band if already above VWAP). Adds P&L mainly on the parent's losing days.

Backtest 2021-08..2026-08, cost $5.40/contract round-turn: net ≈ $18.7k on $22.5k, CAGR ≈ 12.9%,
Sharpe ≈ 1.47, max drawdown ≈ $2.9k (12.9%). About 74% of P&L is mean-reversion.
Measured live slippage ≈ 1.9 points per round-turn — costs matter a lot at this size.

## Already tried and FAILED — do not propose these again (or explain what is different)

- VIX / VIX3M / VIX9D term-structure day gates (book-wide, TLB-only, short-only) — no out-of-sample edge.
- Overnight (Globex) move as a sizing input — HSI-style rule loses; the inverse is single-era.
- RSI(14) extremes as the VWAP entry trigger — continuation, not exhaustion, on NQ; loses badly.
- Other entry-trigger tweaks (N closes below band, z-score depth, no-confirmation entries) — no
  candidate beats the deployed trigger in both in-sample and out-of-sample halves.
- Opening-range breakout (ORB) — secular decay 2024→2026, retired.
- Options wrappers on TLB/ORB — degraded the edge, retired.
- For R3 re-entry: trailing exits, a second re-entry attempt, blocking re-entry after a same-session
  target — none adopted.
- On a sister HSI futures book: 21 entry features, 6 exit triggers, 9 stop variants — all null; one
  order-block-based early exit failed because banked small wins cost more than the lost big tail.

## One thing that DID work (on the HSI book) — position management

**Early abort**: if a trade has not gone at least a little in its favour by bar 15, and has gone
against it by a set amount, close it early. Validated three times (walk-forward, portfolio basis) and
deployed live. The owner wants more ideas in this family: **continuous checks on open positions and
market conditions that manage risk before the market turns for the worse.**

## What to produce — `out/IDEAS.md`

For each idea:
1. **Name** and one-line summary.
2. **Category**: new setup · entry refinement · exit/target refinement · position management ·
   risk/sizing · regime/filter.
3. **Mechanism** — WHY it should work on NQ specifically (market microstructure, participant
   behaviour). An idea with no mechanism is a curve-fit waiting to happen; say so if you have none.
4. **Exact rules**, precise enough to code with no further questions (bar size, thresholds, what is
   known at decision time — no look-ahead).
5. **Parameters**, and how few: fixed from first principles where possible. Each free parameter
   raises the bar for proof.
6. **Data needed** (the team has 1-min NQ OHLCV 2021→now, daily VIX/VIX3M/VIX9D; anything else,
   say so).
7. **How it could fail / how it could look good falsely** (overfitting, look-ahead, cost drag,
   regime dependence).
8. **Expected effect size** and what would convince you it is real.

Then a final **ranked shortlist of the top 5**, ordered by (plausibility of mechanism) × (ease of a
clean test) × (size of the prize), with one paragraph each on why.

The team will test every idea with: fixed rules (no per-fold rule selection), 24-month -> 6-month
walk-forward, a 200-rep permutation null, drop-top-5 concentration, 1×/2×/3× costs, portfolio basis,
and an explicit count of every variant tried. Design ideas that can survive that.

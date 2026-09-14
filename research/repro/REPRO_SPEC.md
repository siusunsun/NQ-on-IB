# Independent reproduction — NQ "V9 + R3 re-entry" backtest

You are building a backtest **from this written specification only**. A separate team built the
original. Your job is to reproduce it independently so the two can be compared trade by trade; any
difference means one of the two implementations has a bug (or this spec is ambiguous).

**Rules of engagement**
- Use ONLY the files in this folder (`REPRO_SPEC.md`, `data/`). Do not search for, open or copy
  any other code on this machine — independence is the whole point of the exercise.
- Write all your code under `repro/` and all outputs under `out/` in this folder.
- Follow the spec **literally**, including rules that look odd. If a rule is genuinely ambiguous,
  pick the most literal reading, and record it in `out/AMBIGUITIES.md`.
- Python + pandas are available. Data is ~2 million rows; keep memory reasonable.

---

## 1. Data

`data/nq_1min.csv.gz` — 1-minute NQ futures bars, columns `time,open,high,low,close,volume`.
`time` is the bar **start**, in UTC. Already cleaned and de-duplicated; use it as-is.
Range 2020-12-30 → 2026-08-26.

`data/no_trade_dates.csv` — column `et_date`: US market holidays (New York calendar dates) on which
**no new VWAP or TLB entries** may be taken. (R3 re-entry does not use this list.)

"ET" means `America/New_York` with daylight saving applied.

## 2. 5-minute bars

Aggregate 1-minute bars into 5-minute buckets by flooring the timestamp to 5 minutes.
Bucket timestamp = bucket **start**. open = first open, high = max, low = min, close = last close,
volume = sum. A bucket exists only if at least one 1-minute bar falls in it. **The final bucket in
the data is dropped** (it is never "completed").
Everything below is processed strictly in time order, one completed 5-minute bar at a time.

## 3. Daily closes and regime

- Daily close of ET calendar date D = close of the **last 1-minute bar** whose ET date is D.
- The regime **for** ET date D uses only closes of dates strictly **before** D. It exists only if
  there are at least 205 such closes. With `c` = those closes in date order:
  - `prev_close = c[-1]`, `sma200 = mean(c[-200:])`, `ret20 = c[-1] / c[-21] - 1`
  - `s200 = prev_close > sma200`, `r20 = ret20 > 0`

| cell | s200 | r20 | sleeves allowed to OPEN trades |
|---|---|---|---|
| BOTH_BULL | T | T | TLB_LONG, VWAP_LONG_R3 |
| ZONE_A | T | F | VWAP_LONG_R3 |
| ZONE_B | F | T | TLB_LONG |
| BOTH_BEAR | F | F | VWAP_SHORT_R1 |

A bar's regime = the regime of the ET date of the bar's **start** timestamp. No regime -> no entries.

## 4. Session VWAP (used by the VWAP sleeves and by R3 re-entry)

On 5-minute bars. Session key = the **UTC** calendar date of the bar start (anchor 00:00 UTC).
At each new session reset the sums. Per bar: `tp = (high + low + close) / 3`;
`cumv += volume`, `cumvp += tp*volume`, `cumsq += tp*tp*volume`;
`vwap = cumvp/cumv`, `sd = sqrt(max(cumsq/cumv - vwap^2, 0))`; if `cumv <= 0` both are NaN.
`upper = vwap + 2*sd`, `lower = vwap - 2*sd`.

## 5. VWAP detectors — two independent detector objects, LONG and SHORT

Both are updated on **every** 5-minute bar, regardless of regime, time window or open positions.
Each keeps: its own VWAP sums (section 4), `streak` (int), `lows`/`highs` (lists, max length 20),
`last_bar_time`, `bars_since_halt` (int).

Per bar, in this exact order:
1. If the session key changed: reset the VWAP sums and set `streak = 0`.
2. Update the VWAP sums with this bar.
3. **Halt reset**: if `last_bar_time` is set and (this bar start − `last_bar_time`) > 180 minutes,
   clear `lows` and `highs` and set `bars_since_halt = 0`. Then `bars_since_halt += 1`;
   `last_bar_time = this bar start`.
4. Append this bar's low to `lows` and high to `highs`; drop the oldest while longer than 20.
5. If vwap or sd is NaN, or sd == 0: no signal (stop here).
6. **LONG detector only**: if `bars_since_halt < 24`: no signal (stop here).
7. LONG rule:
   - if `close < lower`: `streak += 1`; no signal.
   - elif `close > lower` and `streak >= 5`: entry = close; stop = min of `lows` **excluding the
     current bar** (if `lows` has only 1 element, stop = this bar's low); risk = entry − stop.
     If risk > 0: **SIGNAL** long, target = entry + 3 × risk, and `streak = 0`.
     If risk <= 0: `streak = 0`, no signal.
   - else: `streak = 0`.
   (Note `close == lower` exactly falls into the last branch.)
   SHORT rule (no step 6): mirror image with `upper`, `streak >= 3`, stop = max of `highs`
   excluding the current bar, target = entry − 1 × risk.

## 6. VWAP sleeves: VWAP_LONG_R3 and VWAP_SHORT_R1

A signal on the bar starting at `t` is TAKEN if all hold:
- the sleeve is allowed by that bar's regime cell (section 3);
- the bar's ET date is not in `no_trade_dates.csv`;
- the ET time of `t`, in minutes after midnight, lies in any window `lo <= m < hi` of
  `[(0,360), (570,720), (720,870), (1140,1440)]`;
- the sleeve is not busy: `entry_time >= exit_time` of that sleeve's previous taken trade.

`entry_time = t + 5 minutes` (the bar's close). Entry price = the signal's entry (the bar close).

**Exit simulation (1-minute bars)** — scan 1-minute bars with timestamp `>= entry_time`, in order:
1. long: stop hit if `low <= stop`; target hit if `high >= target` (short: `high >= stop`,
   `low <= target`). **Stop is checked first and wins ties.** Exit at the stop price, reason `STOP`,
   exit time = that 1-minute bar's timestamp.
2. else target hit -> exit at the target price, reason `TARGET`.
3. else if that bar's ET time is 15:55 or later **and** its ET hour < 17 -> exit at that bar's
   close, reason `GAP_FLATTEN`.
4. Positions otherwise stay open across sessions and nights.
5. Out of data -> exit at the last close, reason `DATA_END`.

`pnl_points = (exit − entry)` for long, `(entry − exit)` for short.

**Wide-stop cap, applied AFTER simulating**: discard any VWAP trade whose initial risk
`|entry − stop|` exceeds 200 points. **Important:** the cap is applied after the fact, so a
discarded trade STILL made its sleeve busy until its exit time (it still blocks later signals).

## 7. TLB_LONG (trend-line break, "hybrid-40" stop)

One detector, updated on **every** 5-minute bar (all hours, all days). Keep full lists `h`, `l`, `c`
of highs, lows and closes, the last 2 pivot highs and last 2 pivot lows as `(index, value)`.
Per bar (append first, `n` = number of bars so far, `k = 3`):
1. Pivot confirmation at `i = n − 1 − k`, only if `i >= k`:
   - pivot high if `h[i] >= max(h[i-k:i])` and `h[i] > max(h[i+1:i+k+1])` -> keep the last 2
   - pivot low  if `l[i] <= min(l[i-k:i])` and `l[i] < min(l[i+1:i+k+1])` -> keep the last 2
2. If `n >= 2` and there are 2 pivot highs and >= 1 pivot low: with pivot highs `(i1,v1),(i2,v2)`,
   `slope = (v2 − v1) / max(1, i2 − i1)`, `line(x) = v2 + slope × (x − i2)`.
   With `now = n−1`, `prev = n−2`: if `c[prev] <= line(prev)` and `c[now] > line(now)`:
   entry = `c[now]`, stop = value of the most recent pivot low, risk = entry − stop.
   Only if risk > 0 is there a candidate signal (target = entry + 1 × risk).
3. **Hybrid stop**: if that candidate's risk < 40 points: `new_stop = min(l[now-19 : now+1])`
   (the last 20 lows including the current bar; fewer if the list is shorter);
   if `entry − new_stop > 0` replace stop with `new_stop` and target = entry + 1 × (entry − new_stop);
   otherwise keep the original candidate.

A signal on the bar starting at `t` is TAKEN if: regime cell is BOTH_BULL or ZONE_B; the ET date is
not a no-trade date; the ET time of `t` is in `[(600,715), (895,920)]` (`lo <= m < hi`);
`(entry − stop) × 2.0 <= 3000`; and not busy (`entry_time >= previous TLB exit time`).
`entry_time = t + 5 minutes`.

**Exit (on the following 5-minute bars)**: for each later 5-minute bar, in order:
if `low <= stop` -> exit at stop, `STOP`; elif `high >= target` -> exit at target, `TARGET`;
elif that bar's start ET time is 15:20 or later -> exit at that bar's close, `EOD`.
Exit time = that bar's start + 5 minutes. Out of data -> last close, `DATA_END`.

## 8. R3 re-entry (overlay on VWAP_LONG_R3), with max_wait = 48

Parents = the **kept** (post-cap) VWAP_LONG_R3 trades whose exit reason is `STOP`. Each parent is
handled independently; re-entries have **no** busy rule and no regime or holiday filter of their own.

Using the 5-minute bar list (index positions), the section-4 VWAP (`vwap5`, `upper5` per bar), and
`bar_index(ts)` = the position of the 5-minute bar starting at `ts`:
- `si = bar_index(floor5(parent.entry_time − 5 min))` (the signal bar)
- `ki = bar_index(floor5(parent.exit_time))` (the bar containing the stop-out)
- skip the parent if either does not exist. `sess` = session key (UTC date) of bar `ki`.

Search, starting `j = ki + 2`, `scanned = 0`, while `j` exists, bar `j` is in session `sess`, and the
ET time of bar `j`'s start is before 15:55:
- if `scanned >= 48`: stop searching (no re-entry).
- `runmax = max(high[ki .. j−1])`. If `high[j] > runmax`:
  `fill = max(open[j], runmax)`; `stop = min(low[si .. j−1])`; `lag = vwap5[j−1]`;
  if `lag` is NaN: `j += 1`, `scanned += 1`, continue.
  `kind = VWAP if fill < lag else UPPER`; `risk = fill − stop`; if `risk <= 0`: no re-entry (stop).
  Otherwise ENTER at `fill` on bar `j` and stop searching.
- `j += 1`, `scanned += 1`.

Entry time = bar `j` start + 5 minutes. **Exit (1-minute bars)** from the first 1-minute bar at or
after that entry time, in order, per bar `t`:
1. if `t`'s session key != `sess`: exit at the **previous** 1-minute bar's close (or at `fill` if this
   is the very first bar scanned), reason `SESS_END`, exit time = that previous bar's timestamp.
2. elif `t`'s ET time is 15:55 or later: exit at `t`'s close, `FLATTEN`.
3. elif `low <= stop`: exit at `stop`, `STOP`.
4. else `lvl` = `vwap5` (if kind VWAP) or `upper5` (if UPPER) of the 5-minute bar starting at
   `floor5(t) − 5 min` (the last COMPLETED 5-minute bar; NaN if that bar does not exist).
   If `lvl` is not NaN and `lvl > fill` and `high >= lvl`: exit at `lvl`, `TARGET`.
5. Out of data -> last close, `DATA_END`.

## 9. Outputs (in `out/`)

- `trades_VWAP_LONG_R3.csv`, `trades_VWAP_SHORT_R1.csv` (post-cap), `trades_TLB_HYB40.csv`,
  `trades_R3_RE_mw48.csv` — columns
  `entry_time_utc, exit_time_utc, direction, entry_px, exit_px, exit_reason, pnl_points`
- `regime.csv` — `et_date, prev_close, sma200, ret20, cell`
- `SUMMARY.md` — trade count, total points, win rate per sleeve; runtime; anything surprising.
- `AMBIGUITIES.md` — every place this spec was unclear and how you resolved it.

Prices are NQ points. Do not apply costs or position sizes; they are applied at comparison time.

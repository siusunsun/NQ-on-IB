# Ambiguities and literal resolutions

- **Initial detector state:** initial values are not explicitly listed. Start VWAP sums and streak at zero, price histories empty, last time/session unset, and bars_since_halt at zero. The first observed bar is count 1, so LONG needs 24 observed bars at startup as well as after a halt. A halt alone does not reset streak; only the stated session/price rules do.
- **Short mirror:** interpret the mirror as close > upper accumulating streak, close < upper after at least 3 triggering, risk = stop - entry, and close == upper resetting streak. Apply the stated common NaN/zero-SD early return before changing streak.
- **Regime output coverage:** regime.csv includes only ET dates present in the input with at least 205 prior observed daily closes. Dates with insufficient history have no regime and are omitted, not assigned a cell. Missing calendar dates do not get synthetic closes.
- **DATA_END timestamp:** use the last 1-minute bar's start for VWAP and R3, and the last retained completed 5-minute bar's start + 5 minutes for TLB. TLB's final price is that retained bar's close. The dropped final 5-minute bucket's constituent minute rows remain usable for daily closes and 1-minute exits.
- **R3 session exit on first scanned minute:** the spec provides fill as the price fallback but does not fully define its timestamp. Use the preceding row's timestamp from the complete 1-minute input (even if before entry); if there is no preceding row, use entry_time. On later session transitions, both price and timestamp come from the previous scanned row. SUMMARY.md reports any resulting exit-before-entry trades.
- **TLB short history:** the parenthetical 'fewer if the list is shorter' governs the hybrid low slice: clamp its left bound to zero instead of using Python negative-index wraparound.
- **Trade CSV conventions:** direction is LONG or SHORT; timestamps are ISO 8601 UTC with Z; no rounding of calculated prices or P&L is applied. Base trades retain signal order; R3 trades retain parent-processing order (each parent's search is independent).
- **Summary conventions:** win rate is trades with pnl_points > 0 divided by all kept trades; zero-P&L trades are not wins. Total points is the unweighted, cost-free sum. Runtime covers loading, simulation, CSV output, and output validation, excluding the separate synthetic tests and final Markdown writes.

## Explicit rules preserved, not treated as ambiguities

- Keep sparse/partial buckets; drop only the final bucket globally. Do not fill missing bars or reset TLB history across gaps.
- UTC dates anchor VWAP/R3 sessions; ET dates determine regime/holidays; ET bar-start times gate base entries. Daily history uses all supplied 1-minute rows.
- VWAP lookbacks hold at most 20 bars *including* the current bar before it is excluded from stop calculation (normally 19 prior lows/highs). Session changes reset sums and streak, not history or halt age.
- Both VWAP detectors and the TLB detector see every completed bar, including bars when their sleeves are busy or disallowed. Detector signals reset streak even when not taken.
- Simulate each accepted VWAP candidate before applying the strictly-greater-than-200 cap, and update its sleeve's busy-until time even when it is discarded. Equality at an exit time permits a new entry.
- TLB's risk filter uses the final hybrid stop and the literal factor 2.0, but no position sizing is applied to P&L.
- R3 starts at ki + 2, counts at most 48 inspected bars, does not inspect the entry bar's intrabar exits, and uses fill < lag for VWAP (equality selects UPPER). Its stop history includes si through j - 1.
- R3's exit target uses the exact timestamp floor5(minute) - 5 minutes; a missing bucket yields NaN, with no carry-forward. Session end, flatten, stop, target priorities are preserved literally. R3 flatten has no hour < 17 restriction.

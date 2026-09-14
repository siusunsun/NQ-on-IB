# NQ book: eleven hypotheses for making more or losing less

These are proposals for testing, not findings. Only `IDEAS_BRIEF.md` informed this document; no market data, outside research, or backtests were used. The reported book statistics are context supplied by the brief, not independently verified. NQ-specific mechanisms below are causal hypotheses, not established explanations of this book's trades.

The emphasis is managing an existing position when its recovery thesis weakens. No proposal replaces the deployed entry confirmation, introduces RSI or VIX gates, sizes from the overnight move, revives ORB or options wrappers, or adds a second re-entry. Eleven standalone candidates are specified. Combining candidates, changing their scope, or changing any threshold creates additional counted variants.

## Shared implementation contract

- **Control:** Freeze the deployed strategy engine, contract mapping, roll handling, daily regime labels, session boundaries, VWAP/band calculation, position limits, and fill assumptions before testing. These are changes to that engine, not a reconstruction of details absent from the brief. Recompute the entire modified portfolio path: changed positions can change eligibility for subsequent trades. Keep the engine's original signal and pivot-confirmation timing, including the three-bar delay needed to confirm a k=3 pivot.
- **Bars and clocks:** Use completed one-minute NQ OHLCV bars, aggregated into five-minute bars on the deployed alignment. ET means `America/New_York`, with daylight saving and the exchange calendar. A decision at a bar's close can first execute at the next available one-minute open, with the control's adverse slippage. Never fill a close-triggered decision at that same close. Candidate #10 alone uses the explicitly specified quote-level timing after the baseline's causal order-submission time, instead of a one-minute fill approximation. Count post-entry five-minute bars only if their entire interval starts at or after the fill. Carry one-minute lookbacks across session boundaries but never across a contract roll; skip a check until its history is complete. Day-based histories use completed earlier trading dates, never the current date.
- **Trade coordinates:** Let direction `s` be +1 for long and -1 for short, actual entry fill be `E`, initial protective stop be `S0`, and initial risk in points be `R = s(E-S0) > 0`. Freeze R at entry. Current signed excursion is `u = s(C-E)/R`. MFE is the greatest nonnegative favorable excursion divided by R, using completed post-entry one-minute highs for longs and lows for shorts. Exclude a partially observed entry minute from MFE. Include current completed observations in a decision, but no later observations.
- **Existing exits:** Original protective orders remain live until an added exit fills. If the original stop or target fills before that time, cancel the added exit. An added exit cancels the remaining original bracket only after its fill. Preserve the control's conservative handling of minutes that touch both stop and target; with OHLC alone, do not assume the favorable ordering. Existing mandatory flattening times always take precedence. Added rules never widen stops or postpone flattening.
- **Re-entry accounting:** Label an added discretionary exit `MANAGEMENT_EXIT`, not `STOPPED`. It does not create a new R3 re-entry right. An actual original protective-stop fill retains the baseline re-entry behavior. Partial reductions do not create re-entry rights; a later actual stop of the remaining parent position does. Include the resulting lost or changed re-entry P&L in the portfolio comparison. Do not introduce synthetic re-entry attempts to make an abort look better.
- **Costs:** Define base round-turn cost `c = $5.40` per contract and MNQ point value `v = $2`, hence cost equivalent `c/v = 2.7` points. The brief does not say whether $5.40 includes the measured 1.9-point slippage. Resolve that from the team's existing cost ledger before the run, without estimating a new favorable assumption. If slippage is separate, the additional measured amount is $3.80 per contract round-turn; if included, do not add it twice. Charge actual legs, including partial exits and eventual liquidation, using the frozen ledger. Apply the team's 1×/2×/3× all-in cost stress consistently to both candidate and control. For signal rules referencing c, keep $5.40 fixed across stresses; stress execution outcomes, not the rule itself.
- **Missing data:** Do not forward-fill missing prices, quotes, or event records into signals. Skip an added decision whose inputs are unavailable; keep the control's protective orders and mandatory exits. Report affected opportunities. External-data candidates must disclose coverage and cannot be treated as tested on the full history unless that history exists.

### What would count as evidence

Use the stipulated fixed rules, 24-month training window followed by six-month out-of-sample windows, 200-rep permutation null, drop-top-5 concentration test, 1×/2×/3× costs, and portfolio accounting. The training window supplies only prescribed historical state; do not optimize thresholds, choose sleeves, or select rules inside a fold. Initial incomplete history produces no decisions. Never use overlapping test observations twice in the stitched out-of-sample result.

Register all eleven candidates, their primary objectives, and every subsequent variation before examining results. Evaluate the team's permutation null with dependence and common NQ exposure preserved; independently shuffling trades from simultaneous sleeves is not credible. Correct for the full searched family, rather than reporting eleven unadjusted chances at significance. With only 200 permutations, a plus-one p-value has minimum resolution 1/201; marginal ranks are weak evidence, not precise significance estimates.

There are two predeclared objectives. **Profit objective** candidates require positive incremental net portfolio P&L at both 1× and 2× costs, positive increments in more than half the complete test folds, and positive incremental P&L after deleting the five ET trading dates with the largest positive candidate-minus-control net contribution. **Risk objective** candidates require lower stitched out-of-sample maximum drawdown, net P&L at least 95% of control when control net P&L is positive (otherwise no deterioration), and lower drawdown at 2× costs with the same P&L-retention condition. Their drawdown improvement must also survive that paired day-removal diagnostic and occur in more than half the complete test folds. Both objectives require support against the team's family-adjusted permutation null, full disclosure at 3× costs, and no dependence on a single test era. A 3× reversal makes a cost-sensitive deployment case unconvincing, even if the base-cost hypothesis survives.

The effect ranges below are **economically worthwhile research targets**, not statistical estimates or promises. No available data justify a positive expected return. Zero or negative improvement is plausible for every candidate. Percentages of net P&L refer to the matched control over the same out-of-sample dates; drawdown reductions are relative reductions in dollar drawdown, not percentage-point returns. The shortlist ranks research priority, not evidence of profitability.

## 1. Failed-rebound early abort

**Summary:** Close an R3 parent that has neither shown meaningful favorable movement nor recovered from a substantial adverse move after 75 minutes.

**Category:** position management.

**Mechanism:** A VWAP-band reversal may benefit from temporary liquidity demand being absorbed. If NQ's index-arbitrage and dip-buying flows do not produce even a modest bounce over 75 minutes, the excursion may represent persistent repricing rather than temporary inventory pressure. This is a transfer of the successful HSI management concept, not evidence that its thresholds transfer to NQ.

**Exact rules:** Apply only to `VWAP_LONG_R3` parents. At every completed five-minute close starting with post-entry bar 15, if lifetime MFE is strictly below 0.25R and `u <= -0.50`, exit all remaining parent contracts at the next one-minute open. Once MFE reaches 0.25R, this rule can never activate for that trade. No new stop, profit trail, or replacement entry is introduced.

**Parameters:** Three free numerical choices: 15 bars, 0.25R MFE, and -0.50R current excursion. Parent-only scope is an additional fixed design choice. Only 15 bars is motivated by the supplied HSI precedent; none is claimed optimal or derived from first principles.

**Data needed:** Existing one-minute OHLCV and control trade/stop records. No additional market feed.

**How it could fail / look good falsely:** NQ may rebound late, so an abort can destroy the 3R tail. It may also eliminate profitable stop-generated re-entries. Excluding those descendants, measuring only saved stop losses, or filling at the decision close would bias results upward. Broad initial stops can make the conditions too rare to matter.

**Expected effect size and proof:** **Risk objective.** Target a 5–15% drawdown reduction while retaining at least 95% of control net P&L. In addition to the common tests, decompose saved losses, foregone parent recoveries, and foregone re-entry profits. All three must be included; HSI success cannot substitute for NQ evidence.

## 2. Moving-VWAP thesis invalidation

**Summary:** Exit a losing VWAP reversal when the session's traded-price center continues moving against it.

**Category:** position management.

**Mechanism:** NQ can transition from a temporary imbalance to persistent index repricing. A band re-entry is less persuasive if new traded volume keeps pulling session VWAP in the adverse direction. This tests deterioration after entry, whereas the failed experiments changed the entry trigger or imposed daily VIX gates.

**Exact rules:** Apply to R3 parents and `VWAP_SHORT_R1`, not re-entry or TLB. At each completed five-minute close from post-entry bar 6 onward, use current session VWAP `Vt` and its value six five-minute closes earlier `Vt-6`. If those observations are in the same UTC-anchored VWAP session, `s(Vt-Vt-6) <= -0.25R`, `s(C-Vt) < 0`, and `u <= -0.25`, exit the entire trade next minute. Otherwise retain it. Skip the check across a VWAP reset; do not retrospectively recalculate earlier VWAP values with later volume.

**Parameters:** Three free numerical choices: six bars, 0.25R adverse VWAP displacement, and -0.25R unrealized excursion. Scope and same-session restriction are fixed. The zero threshold relative to VWAP expresses which side of the traded-price center the position occupies.

**Data needed:** Existing OHLCV and timestamped VWAP values from the control engine.

**How it could fail / look good falsely:** Session VWAP becomes mechanically less responsive as cumulative volume grows; this is partly a time-of-day rule. Heavy adverse volume can mark capitulation immediately before a rebound. Proxy futures volume does not identify institutions or flow direction. Choosing a different VWAP anchor after inspection would be an additional variant.

**Expected effect size and proof:** **Profit objective.** Target a 2–5% improvement in net portfolio P&L. Require the common tests and an audit of long and short contributions and early/late-session activation. A pooled gain entirely attributable to one fortunate short episode would be insufficient.

## 3. TLB failure to hold the broken line

**Summary:** Abandon an unproductive TLB breakout after two closes materially below the original breakout line.

**Category:** position management.

**Mechanism:** A successful NQ breakout may recruit momentum demand and buy-side stop orders. Rapid, persistent acceptance below the crossed line suggests that this incremental demand was exhausted. This tests post-entry structural failure, not a revival of the retired opening-range breakout.

**Exact rules:** For each `TLB_LONG`, save the exact two confirmed pivot highs used for its entry line, including their five-minute bar indices. Define the fixed line `L(j) = p1 + (p2-p1)(j-j1)/(j2-j1)`; extrapolate using those same pivots for the whole trade. Starting after two complete post-entry five-minute bars, exit next minute if both latest closes satisfy `Cj < L(j)-0.25R` and lifetime MFE remains below 0.50R. Compare each close against its own line value. Do not refit using newly confirmed pivots. Preserve the original stop and 15:20 ET flattening.

**Parameters:** Three free numerical choices: two closes, 0.25R line buffer, and 0.50R MFE ceiling. The pivot k=3 and original stop-width rule are inherited, not tuned here.

**Data needed:** Existing OHLCV plus the entry engine's confirmed-pivot identifiers and line equation.

**How it could fail / look good falsely:** NQ often retests a breakout before continuation; this can turn winners into realized losses. A steep descending line may give a weak economic failure boundary. Accidentally choosing later pivots introduces look-ahead. TLB's smaller share of book P&L limits the prize.

**Expected effect size and proof:** **Profit objective.** Target a 1–3% net portfolio P&L improvement. Require the common tests and a complete accounting of aborted trades that the control later took to 1R. Evidence should come from repeated failed breakouts, not one exceptional selloff.

## 4. Re-entry reward collapse

**Summary:** Close a losing re-entry when its contemporaneous VWAP destination no longer offers enough reward for the remaining stop risk and costs.

**Category:** position management.

**Mechanism:** Re-entry is a second attempt to monetize recovery after a failed selloff reversal. Continued adverse volume can drag the recovery destination toward the trade faster than price recovers. For a leveraged, low-point-value MNQ position, holding a stale recovery thesis with little remaining net reward may be uneconomic.

**Exact rules:** Apply only to `R3 re-entry`. At entry, record whether the baseline selected session VWAP or the upper 2σ band as its target type. For this diagnostic only, evaluate that same type using its current value `Tt`; do not switch types after entry and do not change the actual baseline target order. At each complete five-minute close, set remaining reward `G = max(Tt-C,0)` and stop distance `D = max(C-S0,0)`. If `u <= 0` and `G <= 0.50D + c/v` on two consecutive checks, exit next minute. Reset the consecutive-check counter at a UTC VWAP reset, when either condition fails, or when required data are missing. Original exits still take priority.

**Parameters:** Two free numerical choices: reward/remaining-risk ratio 0.50 and two checks. Breakeven eligibility and the supplied cost reference are fixed design choices. The diagnostic's target-type lock is explicit even if the control's actual target order has separate update semantics.

**Data needed:** Existing OHLCV, session VWAP/bands, target-type selection, and re-entry linkage to its stopped parent.

**How it could fail / look good falsely:** Remaining reward-to-risk does not determine win probability; a near VWAP target can be easy to hit. Early exits can remove the parent's best insurance. Session-reset artifacts and retrospectively chosen target types can manufacture triggers. The re-entry sample may be too small for convincing evidence.

**Expected effect size and proof:** **Profit objective.** Target a 1–3% portfolio P&L improvement. Require the common tests and report the combined parent-plus-re-entry distribution on parent losing days. This is neither a trailing exit nor a second attempt: it acts only on a nonprofitable open recovery with a deteriorated reference destination.

## 5. Gross stop-risk ceiling for overlapping exposure

**Summary:** Prevent individually valid entries from consuming too much simultaneous account risk.

**Category:** risk/sizing.

**Mechanism:** TLB and VWAP longs both load on NQ; different entry names do not diversify a common selloff. A ceiling can prevent clustering from turning one underlying market move into several full-size losses. This mechanism is portfolio arithmetic, with NQ's common underlying exposure providing the specific reason it matters here.

**Exact rules:** Before each baseline entry order, calculate liquidation equity `A` from cash plus marked open P&L after realized costs. Use the last available completed one-minute close as the mark `P`. For each open trade i, use its currently active protective stop `Si` and reserve `qi * [v * max(si(P-Si),0) + c]`. Sum reserves gross, without long/short netting. A candidate contract requires `v * max(s(P-Snew),0) + c`, where `Snew` is its baseline initial stop. Admit the largest integer quantity from zero through the baseline requested quantity such that total reserved risk is at most `0.01 * max(A,0)`. Zero means skip this signal, without queuing it for later. Existing positions are never reduced by this rule. Process simultaneous entry decisions in fixed order: R3 parent, TLB, short, re-entry, reserving admitted risk before processing the next. Once admitted, use baseline execution; a fill gap can exceed the pretrade ceiling and must be reported, not retrospectively resized away.

**Parameters:** One free numerical choice: 1% of current equity. Priority ordering, gross risk accounting, and using a full round-turn cost buffer are additional fixed design choices. The 1% threshold is a governance hypothesis, not an optimized or guaranteed loss bound.

**Data needed:** Existing prices, stops, fills, cash/equity ledger, and pending-order state. No additional market feed.

**How it could fail / look good falsely:** It may merely deleverage the book and suppress its strongest two-contract sleeve. Stop losses are not hard bounds during gaps. The chosen priority favors R3 and can explain the result by itself. Comparing drawdown without accounting for reduced average exposure exaggerates skill.

**Expected effect size and proof:** **Risk objective.** Target a 10–20% drawdown reduction with at least 95% P&L retention. Require the common tests, utilization/skipped-signal reporting, and the separately counted always-one-contract R3 benchmark specified in #6, leaving every other baseline sleeve unchanged. Reuse that same benchmark run; do not count it as two distinct rules. Report average gross contract-minutes and initial stop-risk dollars per admitted trade for all three portfolios. If simple R3 size reduction offers the same P&L/drawdown tradeoff, the ceiling has not demonstrated useful allocation beyond basic deleveraging.

## 6. Adverse volatility burst: remove one R3 contract

**Summary:** Halve a losing two-contract parent when adverse one-minute movement becomes much more intense than its immediately preceding background.

**Category:** position management.

**Mechanism:** NQ repricing can accelerate as index hedging and momentum flow reinforce a decline. A stop based on the pre-entry 20-bar low fixes a price boundary but does not update the size of exposure to a new volatility state. Removing one contract retains some chance of recovery while reducing subsequent dollar loss sensitivity.

**Exact rules:** At every completed one-minute close for an R3 parent still holding two contracts, compute signed increments `xt = Ct-Ct-1`. Let `Qrecent` be the mean of `min(x,0)^2` for the latest ten increments, and `Qprior` the mean for the immediately preceding 60 increments, excluding those latest ten. If all data are from the same contract, `Qprior > 0`, `Qrecent >= 4*Qprior`, and `C < E`, sell one contract at the next minute open. Apply at most once per parent. Keep the remaining contract's original stop and 3R target. No add-back, profit trail, or new re-entry right. If another candidate reduced size, that combination is a separate variant; the standalone test starts from the baseline two contracts.

**Parameters:** Three free numerical choices: ten recent minutes, 60 background minutes, and fourfold semivariance. The one-contract reduction follows the deployed two-contract size; the losing-only condition is fixed.

**Data needed:** Existing one-minute close prices and position quantities. NQ bars can proxy direction, but actual MNQ execution uncertainty remains in costs.

**How it could fail / look good falsely:** Downside bursts often mark capitulation. A quiet background makes the ratio unstable, and completed-minute detection can arrive after a gap or stop. Remaining exposure still carries the same point risk. Omitting the large rebounds on the removed contract makes this look artificially attractive.

**Expected effect size and proof:** **Risk objective.** Target a 5–15% drawdown reduction with at least 95% P&L retention. Require the common tests, decomposition of removed-contract outcomes, and a separately counted always-one-contract R3 benchmark. Improvement beyond that benchmark must justify conditional complexity.

## 7. Scheduled announcement exposure release

**Summary:** Remove open mean-reversion exposure before a known scheduled macro announcement can invalidate the pre-event price distribution.

**Category:** position management.

**Mechanism:** NQ's large growth-company exposure makes revisions to interest-rate expectations a plausible source of abrupt repricing. Around scheduled information releases, a previously informative VWAP excursion may become stale and stop fills may worsen. This is scheduled intraday information risk, not a daily VIX term-structure gate.

**Exact rules:** Use only scheduled US CPI releases and scheduled FOMC policy statements. For each announcement at timestamp `e`, at the one-minute close five minutes before e, close all open R3 parents, VWAP shorts, and R3 re-entries at the next minute open. Suspend new entries in those three sleeves from `e-5 minutes` through but excluding `e+10 minutes`; discard signals occurring during that interval. TLB remains governed by the control. Merge overlapping suspension intervals. A re-entry attempt already armed by an actual prior stop keeps its original 48-bar clock; do not pause, extend, or restart it. Do not model unscheduled announcements as if their time were known. Use the release schedule version publicly available at the preceding ET trading-day close; later schedule revisions do not retroactively alter the rule.

**Parameters:** Two free numerical choices: five minutes before and ten minutes after. CPI/FOMC event membership and mean-reversion-only scope are fixed categorical choices, not an invitation to search dozens of release types.

**Data needed:** Additional point-in-time historical announcement schedules with release times, schedule versions, time zones, and cancellations. Existing OHLCV cannot supply these. No event return, surprise value, or revised release content enters the signal. Quote/tick data would improve event-fill realism but are not needed to define the rule.

**How it could fail / look good falsely:** Releases can produce the very reversals this book earns. Positions may rarely overlap eligible events, particularly if the control restricts morning trading. A modern calendar may conceal historical rescheduling. Ordinary slippage assumptions can substantially understate event costs for both strategies, exaggerating or hiding a benefit.

**Expected effect size and proof:** **Risk objective.** Target a 5–15% drawdown reduction with at least 95% P&L retention; zero material effect is plausible with little overlap. Require the common tests, event-by-event exposure counts, and results that survive deleting the five largest benefits. A single avoided FOMC loss is a case study, not validation.

## 8. Close-time recovery feasibility check

**Summary:** Stop carrying a losing mean-reversion trade when the recovery needed before mandatory flattening is unusually large for the time remaining.

**Category:** position management.

**Mechanism:** The book must liquidate at a fixed clock time even if its reversal needs longer. NQ's late-session flows can move price quickly, but a remaining-horizon calculation can distinguish a small unresolved loss from a recovery requiring an unusually large move. The hypothesis is loss reduction as the holding window expires, not a claim that low recovery probability alone makes holding negative-expectancy.

**Exact rules:** Apply to R3 parents and VWAP shorts with the supplied 15:55 ET flattening time. At each completed five-minute close at or after 15:25 ET and strictly before 15:55 ET, let `m` be integer minutes until 15:55. Calculate `sigma = sqrt(mean((Ct-Ct-1)^2))` from the latest 60 completed one-minute increments. If the trade is losing, `sigma > 0`, and adverse distance `-s(C-E) > 2*sigma*sqrt(m)`, exit next minute. Otherwise leave it to its original orders. Apply no special rule to TLB or re-entry, whose deadline details need not be inferred from this brief.

**Parameters:** Three free numerical choices: a final 30-minute monitoring window, 60-minute scale history, and a multiplier of two. Square-root-time scaling is a fixed approximation, not an assertion that NQ increments are Gaussian or independent.

**Data needed:** Existing one-minute prices and the ET trading calendar.

**How it could fail / look good falsely:** The closing auction approach can increase volatility beyond the backward estimate. Mean reversion can make a large adverse excursion attractive, not hopeless. A losing trade can improve expected P&L without reaching breakeven. This may merely realize losses earlier and miss powerful late recoveries; its mechanism is weaker than the early-abort hypothesis.

**Expected effect size and proof:** **Risk objective.** Target a 3–8% drawdown reduction with at least 95% P&L retention. Require the common tests and compare actual control P&L from each exit decision to 15:55, including partial recoveries. A low subsequent breakeven-hit rate alone would not validate it.

## 9. Net-reward cost floor for the 1R sleeves

**Summary:** Skip otherwise valid 1R trades whose gross target cannot support even a modest cost burden.

**Category:** entry refinement.

**Mechanism:** A one-point move pays only $2 per MNQ contract, making execution costs a substantial hurdle for short-horizon NQ signals. A 1R trade with symmetric outcomes needs a win rate above `0.5 + c/(2vR)` just to cover a fixed round-turn cost. Tiny-R opportunities can be poor despite correct directional signals. This leaves the failed entry-trigger designs untouched and tests trade economics after a baseline signal.

**Exact rules:** Apply only to `TLB_LONG` and `VWAP_SHORT_R1`. At the baseline entry decision, use the latest completed one-minute close P and the already known baseline initial stop `Snew`; compute planned risk `Rplan = s(P-Snew)`. Skip the signal if `Rplan <= 0` or `v*Rplan < 5*c`, otherwise submit the unchanged baseline order and quantity. Thus the supplied cost reference gives a planned minimum R of 13.5 points. Never condition admission on a future actual fill; report entries whose fill changes the ratio. Do not postpone skipped signals or alter their stop/target definitions.

**Parameters:** One free numerical choice: gross target at least five times the supplied cost reference. Five is a hypothesis corresponding to a 60% break-even win rate in a simplified symmetric 1R model, not a universal trading law. Sleeve scope is fixed.

**Data needed:** Existing OHLCV, entry-decision timestamps, stop calculations, and the execution ledger. No extra feed.

**How it could fail / look good falsely:** Narrow stops can represent unusually strong opportunities. Costs may depend on conditions rather than being constant. TLB's 40-point stop-widening rule may make this filter almost inert, limiting the prize chiefly to shorts. Removed short trades may also remove valuable insurance. Actual stops and intraday exits mean the simplified win-rate equation is only motivation.

**Expected effect size and proof:** **Profit objective.** Target a 0–2% portfolio P&L improvement; expect low reach. Require the common tests, eligible/rejected counts, and performance on the control's worst 10% of ET daily P&L observations. Tail-insurance deterioration must be shown alongside any average gain. Do not respond to low activation by searching many floor levels.

## 10. Liquidity-aware entry execution

**Summary:** Give a baseline market entry one second to find a normal MNQ spread before paying for immediate execution.

**Category:** entry refinement.

**Mechanism:** The book signals on NQ but trades MNQ. A temporary wide MNQ spread can raise costs without changing the NQ setup; a short wait may reduce that friction. The reported 1.9-point round-turn live slippage motivates measurement, but it does not establish how much is entry spread or recoverable. This changes order timing, not the band trigger or number of confirming closes.

**Exact rules:** Apply to baseline entries in all four sleeves. At the exact order-submission timestamp, inspect the last MNQ best bid and ask received by that time. A quote is usable only if its age is at most 250 milliseconds, both displayed sizes are at least the proposed quantity, `ask >= bid > 0`, and spread is at most two exchange ticks. If usable, submit the original market order immediately. Otherwise wait for the first received quote satisfying those conditions, but no longer than 1,000 milliseconds from the original submission time; submit the original market order then, or at the deadline if none arrives. Never cross a baseline entry cutoff or mandatory flattening boundary: cancel the delayed entry if that boundary arrives before submission. Cancel if the control explicitly withdraws the still-pending order first. No passive limit fill is assumed, no signal is retested, and baseline bracket construction remains unchanged.

**Parameters:** Three free numerical choices: two ticks, 250-millisecond freshness, and a one-second deadline. Displayed size at least order quantity is a mechanical condition. Obtain tick size from the traded contract metadata, not a tunable price threshold.

**Data needed:** Additional synchronized historical MNQ bid/ask prices and sizes, local receive timestamps or a conservatively modeled receive clock, NQ signal timestamps, contract metadata, and IB order/fill timestamps for latency calibration. One-minute NQ OHLCV cannot test this. Best quotes are necessary but do not guarantee a fill at displayed prices; model market execution latency and adverse movement explicitly.

**How it could fail / look good falsely:** A narrow quote can vanish before the order arrives, and delayed entries can chase rebounds. Exchange timestamps without receive latency create fictitious foresight. Ideal top-of-book fills or interpolating quotes from bars would invent savings. A short live sample cannot establish multi-era robustness under the required walk-forward protocol.

**Expected effect size and proof:** **Profit objective.** A useful engineering target is 0.1–0.3 MNQ points saved per executed entry, or $0.20–$0.60 per contract, before resulting changes in trade P&L. Total benefit is this saving times eligible contract entries minus delay-induced P&L loss; no entry count is supplied, so no portfolio-dollar estimate is defensible. Require the common tests on genuinely covered dates plus later live shadow-order verification. Insufficient historical quotes leave this candidate unvalidated rather than exempting it from the protocol.

## 11. Persistent cross-index divergence during a losing long

**Summary:** Exit an unproductive NQ long when it is materially underperforming ES and that relative weakness persists.

**Category:** position management.

**Mechanism:** NQ shares broad equity risk with ES but has concentrated technology/growth exposure. If the broad market is holding up while NQ keeps weakening, sector-specific selling may make an index-wide rebound thesis unreliable. This uses contemporaneous relative behavior after entry, not overnight sizing, RSI, or a VIX gate. The relative-strength mechanism is plausible but unverified for these setups.

**Exact rules:** Apply to R3 parents and TLB longs, not re-entry. On each five-minute close, compute nonoverlapping five-minute log returns `n` for NQ and `e` for ES. From the preceding 20 completed ET trading dates, using only aligned 09:30–16:00 bars, estimate `beta = sum(e*n)/sum(e*e)` with no intercept and no clipping. Freeze beta for the next ET date. Using that beta, calculate historical residuals `r = n-beta*e`, their mean `mu`, and sample standard deviation `h` on those same history bars. At each regular-session close after at least three complete post-entry bars, form `Z = (sum of latest three residuals - 3*mu)/(h*sqrt(3))`, using three same-date regular-session returns. If `h > 0`, `Z <= -2`, `u <= -0.25`, and lifetime MFE is below 0.25R on two consecutive eligible five-minute checks, exit next minute. Reset the counter on any failed or missing check or ET-date change. Disable the rule when regression denominator is zero, history is incomplete, or any return straddles a contract roll. Do not interpret Z as a calibrated Gaussian probability.

**Parameters:** Six free numerical choices: 20 historical dates, three residual bars, Z threshold -2, current excursion -0.25R, MFE ceiling 0.25R, and two consecutive checks. Regression form, regular-session scope, and eligible sleeves are additional fixed choices. This has the largest estimation burden among the management candidates and deserves a higher proof bar.

**Data needed:** Additional one-minute ES OHLCV with contract/roll metadata, synchronized with existing NQ data. Daily VIX cannot substitute for intraday ES returns. Use executable contract histories and exclude artificial roll jumps.

**How it could fail / look good falsely:** Relative underperformance can be the strongest reason to expect an NQ rebound. Beta can vary intraday, and residuals are serially dependent and heavy-tailed. Synchronized-bar mistakes, differently adjusted rolls, and selecting the hedge index after seeing outcomes can manufacture divergence. Extra parameters increase search risk.

**Expected effect size and proof:** **Profit objective.** Target a 2–5% net portfolio P&L improvement. Require the common tests, stable rolling-coefficient diagnostics, and a separately counted NQ-only ablation that sets beta to zero while leaving every other rule fixed. The full rule must beat that ablation out of sample to support the cross-index explanation; otherwise it is merely another adverse-momentum exit with an extra feed.

## Ranked shortlist: top five

**1. Failed-rebound early abort (#1).** It most directly answers the owner's request and has a supplied precedent in position management, while requiring only existing data and a clean three-threshold test. R3's two-contract size and the book's mean-reversion concentration make the potential prize meaningful. Its main burden is accounting for both delayed 3R winners and stop-generated re-entry profits; that burden is clear and testable. The HSI precedent raises research priority, not assumed NQ efficacy.

**2. Moving-VWAP thesis invalidation (#2).** This checks whether the reference price underlying a reversal is still moving against the position, giving it a direct economic connection to the setup. It uses available data, spans both mean-reversion directions without tuning separate thresholds, and reaches the dominant source of book P&L. Time-of-day dependence and capitulation risk keep it below the simpler early abort, but it offers a different observable failure mechanism.

**3. Gross stop-risk ceiling (#5).** Simultaneous sleeves on the same index create a transparent concentration problem, so plausibility is high and implementation is straightforward with the existing ledger. The prize is reduced portfolio loss rather than a new signal edge. Integer MNQ sizing and the requirement to retain 95% of P&L make this a demanding hypothesis; comparison with constant exposure reduction is essential to distinguish useful allocation from simply taking less risk.

**4. Adverse volatility burst reduction (#6).** This continuously updates dollar exposure when the market state changes, while keeping one parent contract available for recovery. It addresses the requested management family and can be tested from available minute bars. The two-contract R3 sleeve gives it practical leverage over portfolio risk, but burst detection may arrive at capitulation. Its conditional rule must earn its complexity against always trading one contract.

**5. TLB failure to hold the broken line (#3).** The broken line provides an observable, frozen reference for whether momentum demand persisted, and confirmed pivot records make the test clean. It earns a place for mechanism and ease despite a smaller potential contribution than the mean-reversion proposals. It ranks ahead of scheduled-event avoidance and execution changes because those require missing historical feeds, and ahead of cross-index divergence because it has fewer parameters and no additional market model.

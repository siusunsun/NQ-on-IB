# Red-team review: R3 re-entry and the NQ book

**Decision as of 2026-09-14:** the supplied history supports a promising re-entry overlay, but does **not** establish the claimed robust, selection-adjusted case for deploying `max_wait=48`. Recommend pausing that overlay and collecting forward paper results under a frozen specification. This is a recommendation; this review has not changed live trading.

The published dollar arithmetic reproduces. The strongest failures are the incomplete treatment of selection, a cap advantage explained by two recent avoided losses, the day-level concentration test, and interpreting one historical drawdown difference as the overlay's risk budget. Dependence-preserving resampling does **not** destroy the overlay's unadjusted profitability or diversification evidence; those favorable results are reported below.

## Scope and reproducibility

Only `BRIEF.md` and files in `data/` were used. No outside research, quotes, execution records, or market prices were obtained. Trade execution is accepted as faithful to the rules, as instructed; this review attacks the inference.

Reproduce from this folder with:

```powershell
python work/review.py
python work/followups.py
```

Scripts and detailed results are in `work/`: `results.json`, `followups.json`, `daily.csv`, `overlay_trades.csv`, and `sweep_current_cost.csv`. `results.json` records input SHA-256 hashes. The calculations independently reproduce the battery's portfolio and original walk-forward outputs. Arithmetic checks reconcile trade, day, and portfolio totals.

The sample runs **2021-08-27 through 2026-08-25**, or **4.99384 years**. There are 342 long-parent trades at two contracts, 222 short trades at one, and 672 TLB trades at one: **1,578 base contract round-turns**. Deployed re-entry adds **161 trades on 146 dates**, at one contract each. Net P&L is gross less **$5.40 per contract round-turn** throughout. Base trades are assigned to New York entry dates to reproduce the battery; re-entry uses its supplied `dt`. The battery's calendar is the union of all weekdays and observed trade dates: **1,321 rows**, including 18 weekend dates. Drawdown calculations include initial equity.

Here, **BREAKS IT** means a stated validation conclusion is unsupported or contradicted, not that future expected profit is proved negative. **WEAKENS IT** identifies material uncertainty. **SURVIVES** means the particular challenge did not defeat the claim under the stated assumptions.

## 1. The search family is larger than the four walk-forward candidates

**Claim attacked:** the selected overlay has demonstrated robust value after selection; only choosing `max_wait` requires validation.

**What I computed.** The sweep contains **14 evaluated rows representing 11 distinct configurations**, counting the shared baseline once: `wait=2`, `stop_src=si`, `magnet=adaptive`, and `max_wait=None` repeat the same baseline. Thus **11 is the minimum documented distinct trial count for this overlay search**; 14 is a conservative row-count sensitivity. Four ignores upstream choices. A 144-cell full factorial was not supplied or documented as tested, so I do not invent it. Earlier strategy, sleeve, sizing, or metric searches could enlarge the family, but their count is unknowable here. Correlated trials are not 11 independent experiments; dependence does not invalidate a Bonferroni correction over 11 tested hypotheses.

I used an equivalent to deflated performance inference: a **one-sided centered joint block-bootstrap test of zero incremental mean**, followed by family correction. Each sample draws the same day blocks for the base book and all four available overlays. For the selected overlay, the null tail compares `sum(resampled R - mean(R))` with observed `sum(R)`. Family-adjusted p is `min(1, 11 × p)`. This does not require missing trial return correlations, although it is conservative and the underlying bootstrap remains approximate.

| Block length, calendar rows | Unadjusted centered-null p | Adjusted, 11 configurations | Adjusted, 14 rows |
|---:|---:|---:|---:|
| 1 | 0.0209 | 0.2301 | 0.2928 |
| 5 | 0.0119 | 0.1311 | 0.1668 |
| 20 | 0.0050 | 0.0550 | 0.0700 |
| 60 | 0.0082 | 0.0907 | 0.1155 |
| 126 | 0.0147 | 0.1622 | 0.2065 |
| 252 | 0.0160 | 0.1760 | 0.2240 |

Each row uses 12,000 resamples. The 20-row result is borderline, not evidence of a sharp distinction at 5%: its approximate Monte Carlo standard error is 0.00064 before multiplying by 11. A different asymptotic method, Newey-West with lag 20, gives unadjusted p **0.00247**, or **0.0272** after multiplying by 11; lag 126 gives adjusted p **0.0951**. Method and dependence assumptions materially affect significance. Selecting whichever method passes would repeat the original error.

As a secondary, explicitly stylized deflated-Sharpe diagnostic, daily standalone Sharpe is **0.05899** (annualized **0.9364**), daily skewness **11.48**, and Pearson kurtosis **238.69**. Using null Sharpe standard deviation `1/sqrt(T−1)` and the Gaussian expected maximum across N independent trials yields DSR-style probabilities **0.933 for N=4**, **0.763 for N=11**, and **0.711 for N=14**. These are assumption-sensitive approximations, not calibrated probabilities that the strategy is profitable: sparse, heavy-tailed returns, serial dependence, and trial correlation violate their convenient assumptions. The joint-bootstrap family correction is the primary diagnostic.

**How many dollars are selection?** It is **not identifiable** from this selected sample. The cap's observed improvement over uncapped re-entry is **$956.05**, or **26.35%** of $3,628.77; that is an observed contrast, not an estimate of selection bias. Seven other distinct configurations lack daily trade paths, and none of these returns is a fresh holdout for the entire search. A numerical claim that exactly some fraction of $3,629 is overfit would invent information.

**Verdict: BREAKS IT.** The declaration that the complete search has passed validation is unsupported. There is positive unadjusted evidence, but no stable family-adjusted result across reasonable assumptions.

**What the team should do:** register the full research family and objective, freeze all parameters and sizing, and evaluate the complete selection procedure inside nested chronological validation followed by untouched forward data. Supply daily paths for every configuration. Do not treat `None` as an untouched control merely because it is called the “pure spec.”

## 2. The cap's entire advantage comes from two avoided losses in 2026

**Claim attacked:** `max_wait=48` itself adds a repeatable improvement over uncapped re-entry.

**What I computed.** Only **13 dates** have different daily outcomes between 48 and None. The two largest differences are:

| Date | Uncapped re-entry net | 48 re-entry net | Improvement |
|---|---:|---:|---:|
| 2026-01-20 | −$347.90 | $0.00 | +$347.90 |
| 2026-03-06 | −$609.40 | $0.00 | +$609.40 |
| All remaining dates combined | | | **−$1.25** |

Those two losses total **$957.30**, exceeding the full-sample cap advantage of **$956.05**. The cap's advantage through 2025 is **−$1.24**; in 2026 it is **+$957.30**. A paired 20-row block bootstrap gives a 95% percentile interval for 48-minus-None of **[−$279, +$2,610]**, and centered-null p **0.111**, before any trial correction.

At current costs, **24 earns $3,638.10**, slightly more than 48's **$3,628.77**; their daily P&L correlation is **0.9971**. The old-cost rounded sweep ranked 48 at $4,170 above 24 at $4,149. Repricing the sweep reverses that ranking. These small differences do not justify switching live to 24; they show how little precision supports the exact setting.

**Verdict: BREAKS IT.** The supplied data do not establish a robust advantage for this cap. Avoiding late losing entries may be sensible, but two historical losses do not validate it.

**What the team should do:** predeclare the cap comparison, retain paired candidate/control outcomes, and require independent future evidence. Do not retune to whichever nearby cap now leads.

## 3. Walk-forward is positive, but does not validate the full strategy or reliably choose the cap

**Claim attacked:** “max_wait IS selectable OOS” and the overlay passes walk-forward.

**What I computed.** The original 24-month training / six-month testing schedule reproduces exactly:

| Test begins | Selected cap | Selected training trade count | Test trade count | Test net |
|---|---|---:|---:|---:|
| 2023-08 | None | 39 | 35 | −$157.40 |
| 2024-02 | None | 68 | 24 | −$218.29 |
| 2024-08 | 48 | 85 | 16 | +$804.15 |
| 2025-02 | 48 | 98 | 12 | −$22.86 |
| 2025-08 | 48 | 83 | 26 | +$565.00 |
| 2026-02 | 24 | 72 | 13 | +$1,237.68 |

Stitched adaptive net is **$2,208.29**, versus None **$1,276.31**, fixed 48 **$2,364.54**, and fixed 24 **$2,429.73** on those same dates. Only **3/6 folds are profitable**; the final fold supplies **56.0%** of adaptive net. Training P&L margins over the runner-up are only **$24–$132**. Resampling each training window jointly in 20-row blocks, 4,000 times, reproduces the original 48 choice in just **38.2%**, **54.2%**, and **48.7%** of its three selected folds. These are stability diagnostics, not probabilities that a choice is correct.

I examined **105 schedules**: training lengths {12,18,24,30,36} months, test lengths {3,6,12}, and every month offset within each test length. All 105 adaptive OOS totals remain positive, ranging **$15–$3,210**. But only **72/105** outperform uncapped re-entry; relative performance ranges **−$556 to +$939**. These schedules overlap and are not 105 independent experiments or a vote establishing significance.

Changing only the original six-month fold offset preserves a positive advantage over None in all six cases, **+$718 to +$932**, although adaptive totals range **$1,281–$2,801** because coverage changes. To separate coverage from window choice, I also held test dates fixed at **2024-08 through 2026-07**. On that identical period, None earns **$1,652.01**; 12-month train/12-month test earns **$1,421.95**, while 24/6 earns **$2,583.98**. Hence selectability still changes with window length after equalizing dates.

The original schedule excludes August 2026, containing **+$447.84** of deployed-overlay P&L, because no complete six-month test remains. That exclusion is normal, but its OOS total does not validate every dollar in the five-year headline. More fundamentally, selecting `wait`, `stop_src`, and `magnet` on the full history leaks information into every claimed test fold. This evaluates an adaptive max-wait selector conditional on an already selected rule, not a fully out-of-sample strategy and not specifically fixed 48.

**Verdict: WEAKENS IT.** Positive stitched OOS P&L survives substantial perturbation; reliable cap selection and fully honest OOS validation do not.

**What the team should do:** freeze the evaluation schedule before looking at results, compare fixed and adaptive policies on identical dates, and nest every parameter choice within training. Report trade counts, fold losses, uncertainty, and excluded tail dates instead of a binary comparison of two totals.

## 4. Correcting dependence does not erase the unadjusted overlay edge

**Claim attacked:** the trade-IID bootstrap establishes confidence in marginal portfolio value; alternatively, 96% parent-day overlap necessarily means harmful duplication.

**What I computed.** **140/146 re-entry dates (95.9%)** overlap the long parent; **118/146** are parent-loss days, supplying **$2,923.12** of overlay net. But daily P&L correlation is **−0.182** with the parent and **−0.188** with the whole base book; on re-entry dates these become **−0.273** and **−0.326**. For example, on 2026-06-09 the base loses **$903.20** and re-entry gains **$1,074.96**, leaving **+$171.76** combined. Dependence exists, but its observed P&L effect is partly diversifying. Same-day occurrence alone does not establish independence or adverse correlation.

The paired circular moving-block bootstrap draws contiguous rows of `[base, None, 12, 24, 48]`, using identical indices for every column. This preserves contemporaneous dependence and within-block ordering; it never shuffles the overlay away from its parent day. With 20-row blocks, the uncentered 95% percentile interval for overlay net is **[$1,290, $6,331]**, and for marginal annualized Sharpe it is **[+0.089, +0.512]**. At 60 rows these are **[$1,137, $6,493]** and **[+0.077, +0.509]**. Every tested block length from 1 to 252 has a positive lower percentile endpoint for these two statistics.

The battery's `P(net<=0)` is the fraction of an **uncentered empirical bootstrap** below zero, not a posterior probability that the strategy loses and not generally a null-test p-value. For 20-row blocks the corresponding fraction is **0.00042**, while the explicitly centered one-sided null p is **0.0050**. Skew makes these different. These selected-strategy percentile intervals are not selection-adjusted confidence intervals.

**Verdict: SURVIVES** the dependence-only challenge to positive unadjusted marginal value. The original IID bootstrap is an inadequate portfolio analysis, but a better bootstrap does not automatically produce a negative result.

**What the team should do:** use joint day/block paths and distinguish confidence intervals, null p-values, and empirical loss fractions. Preserve parent linkage when collecting new data. Randomizing which eligible stop-outs receive re-entry cannot be done credibly here: timestamps, parent IDs, and counterfactual outcomes for unfired opportunities are absent.

## 5. “Only +$32 drawdown” is a path statistic, not a dependable risk allowance

**Claim attacked:** $3,629 was earned for only $32 of additional risk.

**What I computed.** Base maximum daily drawdown is **$2,860.50**, combined **$2,892.98**, a difference of **$32.48**. Both paths' maximum dollar drawdown runs from **2024-06-11 to 2024-09-09**. Re-entry earns **−$32.48** after that peak through that trough, exactly explaining the headline difference. Subtracting the two maxima measures what happens in this particular worst episode; it is not the maximum adverse incremental effect across all episodes.

| Joint block length | Median change in max DD | 95% percentile range | Fraction with increase above observed $32.48 |
|---:|---:|---:|---:|
| 1 | −$303 | [−$1,942, +$422] | 20.7% |
| 5 | −$233 | [−$1,404, +$392] | 21.6% |
| 20 | −$345 | [−$1,534, +$255] | 12.9% |
| 60 | −$251 | [−$1,219, +$264] | 22.6% |
| 126 | −$234 | [−$1,256, +$239] | 28.6% |
| 252 | −$121 | [−$1,125, +$221] | 29.5% |

The bootstrap often **reduces** drawdown, so the observed $32 increase is not shown to be an unusually lucky outcome. Nevertheless, plausible resampled increases are several hundred dollars. Within calendar year 2023, adding re-entry increases maximum DD by **$220.82**; in 2025 and 2026 it reduces it by **$474.21** and **$477.23** respectively.

Checks that did not break the number: removing the top 1, 3, 5, or 10 overlay-profit dates **jointly from both books** leaves the full-history DD difference at $32.48. Permuting the six intact calendar-year blocks in all **720** orders produces changes from **−$814.82 to +$32.48**. Thus I cannot claim the $32 arose solely from the ordering of a few winning days. The broader failure is interpreting a single whole-period maximum difference as a stable marginal-risk estimate.

**Verdict: WEAKENS IT.** Historical arithmetic survives; the $32 risk-budget interpretation does not.

**What the team should do:** report distributions and period-specific DD changes, choose a risk allowance before deployment, and measure marked-to-market portfolio exposure and drawdown. Do not budget incremental risk at $32 or turn the ratio $3,629/$32 into an expected reward-to-risk estimate.

## 6. The concentration “pass” fails at the natural day unit

**Claim attacked:** positive net after deleting ten best trades establishes a broad edge.

**What I computed.** Deleting the best **3/5/10/11 trades** leaves **$1,752.56 / $1,177.44 / $68.98 / −$97.28**. Ten trades carry **98.1%** of total net. Deleting the best **3/5/10 dates** leaves **$1,665.85 / $926.50 / −$228.75**. Ten days carry **106.3%** of net. Deleting whole dates from both candidate and control produces this same marginal-net subtraction, without retaining base losses selectively.

The ten largest overlay-profit dates are:

| Date | Overlay net |
|---|---:|
| 2026-06-09 | $1,074.96 |
| 2026-07-08 | $471.62 |
| 2025-01-14 | $416.34 |
| 2025-10-31 | $409.73 |
| 2026-03-05 | $329.62 |
| 2026-08-06 | $319.20 |
| 2025-11-18 | $244.21 |
| 2026-08-24 | $214.04 |
| 2021-11-23 | $202.69 |
| 2024-04-24 | $175.11 |

Eight of these ten dates are in **2025–2026**. The largest single trade/day is **29.6%** of total overlay net. Its gross payoff is **540.18 NQ points** at $2 per point, so accurate representation of rare large payoffs matters far more than the sign of a $69 residual. No market regime labels or bar data were supplied; I can identify the period and payoff concentration, but cannot attribute it to a particular macro event or volatility regime.

**Verdict: BREAKS IT.** A robust concentration pass is not supported. The literal trade-level result is true, but changes sign with one more trade or a sensible change in sampling unit. Tail concentration does not itself prove a tail-paying strategy has no edge.

**What the team should do:** predeclare day/episode concentration tests and report them jointly with costs. Validate repeatability of rare large payoffs in untouched data; do not select the deletion count because the residual just stays positive.

## 7. The era split hides a substantial recent-period dependence

**Claim attacked:** positive halves and five positive years out of six establish regime breadth.

**What I computed.** Annual deployed-overlay results are:

| Year | Trades | Net |
|---|---:|---:|
| 2021, partial | 6 | +$290.10 |
| 2022 | 2 | −$5.60 |
| 2023 | 55 | +$396.19 |
| 2024 | 40 | +$245.52 |
| 2025 | 40 | +$1,095.84 |
| 2026, partial | 18 | +$1,606.72 |

Before 2025: **103 trades, $926.21, $8.99 per trade**. Since 2025: **58 trades, $2,702.56, $46.60 per trade**. Thus **74.5%** of net comes from **36.0%** of trades in the recent period. Only **12 of 21** calendar quarters touched by the sample are positive; three quarters have no fires. Six named years do not provide six equally informative replications when 2022 contains two trades.

The four largest 2026 trades total **$2,195.41**; the rest of 2026 loses **$588.69**. Before 2025, deleting its five best trades leaves only **$121.11**. Conversely, deleting any one entire calendar year still leaves positive total net; removing all of 2026 leaves **$2,022.05**. Therefore “all profit exists only in 2026” would also be false.

The battery's positive-half results, approximately **$779** and **$2,850**, are compatible with a much weaker older edge and a few powerful recent outcomes. They do not test equality or stability of expected returns across regimes. The 2025 cutoff here is an exploratory sensitivity, not a newly confirmed regime model.

**Verdict: WEAKENS IT.** Older profitability survives, but temporal breadth is much less persuasive than the era-pass label implies.

**What the team should do:** report quarterly exposure and P&L, predefine regime variables independently of profits, and gather forward evidence across different conditions. Do not introduce a newly optimized regime filter from this review.

## 8. Overlay cost headroom survives; execution realism is unverified and whole-book 3× costs fail

**Claim attacked:** the overlay and deployed book are robust to realistic costs and pass 3× costs.

**What I computed.** Overlay gross is **$4,498.17** across 161 trades. Its historical constant-cost break-even is **$27.9389 per round-turn**, or **5.174×** the required $5.40. Keeping commission at $1.22, that corresponds to total round-turn slippage of **13.3595 points** at $2/point. This is a retrospective average-cost threshold assuming the same trades and gross outcomes, not a live execution estimate.

| All-in cost / contract round-turn | Overlay net | Base net at same cost | Combined net at same cost |
|---:|---:|---:|---:|
| $5.40 | +$3,628.77 | +$15,060.80 | +$18,689.57 |
| $10.80 | +$2,759.37 | +$6,539.60 | +$9,298.97 |
| $16.20 | +$1,889.97 | −$1,981.60 | **−$91.63** |

The supplied battery stresses only overlay costs and keeps the base book at 1×. That is a valid **marginal** stress: the overlay remains positive at 3× even against a consistently stressed base. It is not a full-book robustness test. The combined book's constant-cost break-even is **$16.1473**, just below 3×.

After deleting the best ten overlay trades, the remaining 151 trades have break-even cost only **$5.8568**. Another **$0.4568 per round-turn**, equivalent to **0.2284 points** of extra total slippage, eliminates the $68.98 concentration residual. Testing costs and concentration separately hides this fragility.

There is also a ledger inconsistency: **1.88 points × $2 + $1.22 = $4.98**, not $5.40. The mandated $5.40 implies **2.09 points** of slippage if commission is $1.22. Using $4.98 would add only **$67.62** to overlay net. This mismatch does not create the claimed profit, because the required $5.40 is more conservative, but it prevents exact reconciliation of the execution story.

Neither the gross trade lists nor the sweep contain actual MNQ quotes, fill delays, entry/exit slippage, commissions, spread, or conditional execution measurements. They cannot establish whether $5.40 is realistic for breakout stop entries, or whether slippage covaries adversely with the few important payoffs. A flat-cost survival test does not model missed fills or changed trade outcomes.

**Verdict: SURVIVES** the overlay's constant 3×-cost test; **BREAKS IT** if that is presented as a whole-book 3× pass; **WEAKENS IT** on live execution realism.

**What the team should do:** reconcile the cost ledger and measure actual MNQ entry and exit shortfall against decision-time benchmarks, separated by stop-entry conditions and liquidity. Stress every sleeve consistently and combine execution and concentration scenarios. Do not replace the required cost with $4.98 merely to improve the report.

## 9. Headline arithmetic is honest under its conventions, but needs precise labels

**Claim attacked:** CAGR 12.9%, Sharpe 1.47, maximum DD 12.9% on a $22,500 base are correct; sparse trading makes the reported Sharpe illegitimate.

**What I computed.**

| Metric | Three-sleeve base | Base + 48 |
|---|---:|---:|
| Net | $15,060.80 | $18,689.57 |
| Ending account equity | $37,560.80 | $41,189.57 |
| Endpoint CAGR | 10.8065% | **12.8719%** |
| Daily fixed-base P&L Sharpe, √252 | 1.1718 | **1.4707** |
| Maximum daily dollar DD | $2,860.50 | **$2,892.98** |
| Dollar DD / initial $22,500 | 12.7133% | **12.8577%** |
| Maximum DD / preceding equity peak | 10.0151% | **9.9105%** |

CAGR is `((22500 + net)/22500)^(1/4.99384) − 1`, with fixed specified contract sizes and no cash flows. It is a valid endpoint annualized account growth statistic, not a demonstration that position size was compounded at a constant risk fraction. Returns calculated as daily P&L divided by prior account equity give Sharpe **1.5060** for the combined book instead of 1.4707. The reported 12.9% DD explicitly uses initial capital; a conventional peak-equity percentage gives 9.91%, so the reported denominator is conservative but different.

The battery **already includes zero-P&L weekdays**. The combined book is active on **845/1,321 rows (64.0%)**, not a minority; the overlay alone fires on 11.1%. Removing inactive rows and incorrectly applying √252 would give combined Sharpe **1.8429**. That inflation is **not** present in the published number. Including zero returns for genuinely idle capital is appropriate.

The calendar is approximate: weekday holidays remain and 18 weekend entry dates are separate observations. Moving weekend P&L to the next business date, keeping every dollar, gives Sharpe **1.4834**. A full calendar-day series with √365.25 gives **1.5048**. These are transparent sensitivities, not exact exchange-session reconstructions, and do not reveal a materially inflated 1.47. All use zero cash/risk-free return and conventional square-root annualization; serial dependence and changing exposure still limit interpretation.

**Verdict: SURVIVES** the arithmetic and inactive-day challenge. The labels need to say “fixed-size historical account CAGR,” “daily P&L Sharpe with zero cash benchmark,” and “daily dollar DD as a percentage of initial capital.” A high in-sample Sharpe does not cure the selection problem.

**What the team should do:** publish those conventions and the full daily series, use a consistent exchange-session calendar, and separate historical account arithmetic from statistically supported forecasts.

## 10. Trade lists cannot establish true marked-to-market drawdown

**Claim attacked:** the reported daily DD and Sharpe fully describe live account risk.

**What I computed.** **27 base trades cross New York dates**: 16 long-parent and 11 short. The battery assigns their entire eventual P&L to entry dates. Reassigning base P&L to exit dates, leaving overlay dates unchanged because no intraday timestamps were supplied, changes base Sharpe from **1.1718 to 1.1462**, and combined Sharpe from **1.4707 to 1.4217**. It leaves endpoint net, CAGR, and the maximum daily dollar DD unchanged in this sample.

For the base alone, ordering realized exits by timestamp gives maximum closed-trade drawdown **$3,014.90**, versus **$2,860.50** after daily netting: **$154.40 more**, already larger than the advertised overlay increment. This is an illustration of information lost by daily aggregation, not a reconstructed combined intraday DD. Open-position marks and overlay timestamps are absent, so neither realized-exit ordering nor entry-date aggregation yields a genuine marked-to-market combined equity path.

The script also omits the initial zero in its running drawdown peak. I corrected that convention in this review; it does **not** change the published maxima here. This is an accounting detail, not an attack on the independently reproduced trading implementation.

**Verdict: WEAKENS IT.** Daily realized-P&L summaries are reproducible, but actual intraday or marked-to-market account risk remains unmeasured.

**What the team should do:** retain timestamped overlay fills and produce one shared marked-to-market account ledger with all open positions, costs, and cash. Report intraday and daily DD separately before treating the daily headline as a live risk limit.

## Statistical limits of this review

The circular block bootstrap is conditional on these observed regimes and payoffs. It preserves dependence within each block, breaks dependence across block boundaries, and wraps the end of the sample to its start. At 252 rows there are only about five blocks per resample. Long-block results therefore do not establish resilience to a genuinely new regime. The percentile DD ranges are resampled historical-path diagnostics, not prediction intervals for live risk. No resampling method can manufacture an unseen market state or undo all upstream selection without the missing candidate paths.

For the optional DSR-style calculation, `s = mean(R)/sd(R)`, `SE(s) = sqrt((1 − skew*s + (kurtosis−1)*s²/4)/(T−1))`, and the null expected maximum is `[ (1−γ)Φ⁻¹(1−1/N) + γΦ⁻¹(1−1/(N*e)) ]/sqrt(T−1)`, where γ is Euler's constant; the reported value is `Φ((s−expected_max)/SE(s))`. The independence and null-variance assumptions are explicit, and this approximation is not used to claim an exact dollar haircut.

All alternative cutoffs, fold schedules, and block lengths in this review are diagnostic disclosures. They are not candidates from which to pick a new live winner. The prior reference document contains proposed tests, not an independent empirical validation of this overlay.

## Overall verdict and deployment recommendation

**The strong claim of real, robust, validated value BREAKS IT; the weaker claim of promising historical marginal value survives.** The $3,628.77 increment, 1.4707 Sharpe, and $32.48 daily-DD difference are real calculations on the supplied history. They are not fabricated. Joint dependence-preserving resampling remains favorable before selection adjustment, and reasonable changes to fold boundaries do not generally eliminate positive OOS P&L.

Nevertheless, fixed 48's improvement over None is entirely explained by two 2026 avoided losses, the full search never received honest OOS validation, ten-date deletion turns the edge negative, and the tiny DD increment cannot support a live risk budget. These are material failures of the justification for deploying a supposedly validated overlay.

**R3 re-entry at `max_wait=48` should not stay live on the present validation claim. Recommend pausing it and continuing frozen forward paper collection, with actual execution observations where available, until a predeclared independent evaluation supports deployment.** The supplied data cannot determine a defensible universal minimum number of future trades. Do not automatically switch to None or 24, and do not infer from this recommendation that the entire base book has been independently cleared for live trading.

**The single most important process fix:** make the unit of validation the **entire research-and-selection procedure**. Register every tried configuration and the decision rule, keep all tuning inside training, and reserve genuinely untouched forward evidence for the final deployment decision. Repeatedly applying favorable tests to the already selected winner is the central failure.

# Development-stage red team

**Verdict:** I reproduced all nine frozen development results. None of the implementation corrections demonstrated here changes a frozen PREREG pass/fail: **C2 and C9 remain PASS; C1, C3, C4, C5, C6, C8 and B1 remain FAIL.** Several numbers and execution/accounting behaviors are wrong. The strongest numerical correction is C5's 2x-cost account simulation: net becomes **-$1,012.49**, versus the reported **+$2,184.65**.

There is also a material distinction between passing PREREG and satisfying IDEAS.md. PREREG explicitly replaced parts of the original validation contract. Restoring either the original 24-month history requirement or family adjustment of the matched random null makes **both C2 and C9 fail** in the development-only demonstrations below. These are conditional protocol corrections, not undisclosed changes to the frozen gates. I do not claim an implementation bug has overturned either frozen PASS.

## Scope and reproducibility

All market calculations use ET dates strictly before the seal boundary. Development results cover **2021-08-27 through 2025-08-26**; earlier observations supply historical state only. `work/dev_loader.py` streams the source gzip, rejects the first sealed timestamp before parsing its prices, and stops. It rebuilds signals and indicators from that permitted prefix into `work/redteam_dev_signals.pkl`. It does not load the full-period cache. Time assertions guard inputs, signals, trade entries, exit legs and activations. The terminal decision bucket is excluded to prevent a decision at the boundary; no control or candidate trade has a synthetic DATA_END exit.

I did **not** run `run_holdout.py`, `run_dev.main()`, the unbounded `gate0.py`, or the unbounded audit. The audit was imported only after installing the development-only data holder. Reference trade CSV rows were filtered by timestamp text before numeric conversion. Every file I created or edited is this report or under `work/`; Python bytecode writing was disabled.

Commands, run from the project directory in this order:

```powershell
python -B work/dev_loader.py
python -B work/redteam_checks.py > work/redteam_checks.txt
python -B work/redteam_edges.py > work/redteam_edges.txt
python -B work/redteam_nulls.py > work/redteam_nulls.txt
python -B work/redteam_c6_null_fix.py > work/redteam_c6_null_fix.txt
python -B work/redteam_stress.py > work/redteam_stress.txt
python -B work/redteam_stats.py > work/redteam_stats.txt
```

The scripts use the supplied simulator and helpers, with isolated in-memory corrections where identified. Detailed results are in the corresponding text/JSON files under `work/`.

## 1. C5 stress uses an account balance that never pays stressed costs

**Location:** `sim.py:397-410`, especially `sim.py:405`; `run_dev.py:209-211`. **Severity: changes a number, substantially; C5 remains FAIL.**

`run_dev` simulates C5 once, then reprices the resulting exit legs at 1x/2x/3x. That works for rules whose future actions do not depend on realized net P&L. C5's next admission explicitly depends on liquidation equity after realized costs, but `Book.equity_and_reserve` always deducts only `COST`, even when reporting a 2x or 3x account. This freezes future admissions at the equity of the 1x account.

**Demonstration:** `python -B work/redteam_stress.py`. Deduct the extra realized exit costs from cash before each admission and recompute the entire book for each stress. Keep the rule's reference cost and reservation buffer at $5.40; this changes realized account equity, not any threshold.

| C5 scenario | Published fixed-path net | Recomputed net | Published DD | Recomputed DD | Admission records differing, including path additions/removals |
|---|---:|---:|---:|---:|---:|
| 1x | $9,355.85 | $9,355.85 | $1,908.80 | $1,908.80 | 0 |
| 2x | $2,184.65 | **-$1,012.49** | $3,010.86 | $3,080.89 | 61 |
| 3x | -$4,986.55 | -$5,904.04 | $7,383.64 | $6,655.02 | 149 |

The 2x net discrepancy is $3,197.13. C5 already fails retention at 1x, so this correction cannot promote it. This experiment isolates cost accounting and retains the original timestamp settlement convention; finding 2 addresses that convention separately.

**Fix:** Distinguish realized execution cost from the frozen rule-reference cost. Pass an execution-cost multiplier into the book and rerun account-dependent sizing at each stress. Fixed-path cost repricing can remain a separately labelled diagnostic, but cannot stand in for the C5 account under stressed execution costs.

## 2. C5 spends intraminute future fills and can erase simultaneous reserved risk

**Location:** `sim.py:403-409`, `sim.py:416`, `sim.py:489-502`. **Severity: changes a number/state; no observed development admission or verdict change from the isolated causal correction.**

Exit timestamps have different meanings across sleeves. A VWAP STOP/TARGET timestamp labels the **start** of the minute whose high/low caused the fill. At an entry decision at that timestamp, its intraminute outcome is not yet known. `tm <= T` nevertheless realizes that outcome and releases all its risk. TLB exit timestamps instead label completed bucket ends, which can already be known at T. Blindly changing every comparison to `<` would mishandle those TLB exits.

**Demonstration:** `python -B work/redteam_checks.py` and `python -B work/redteam_edges.py`.

At **2024-05-17 09:10 UTC**, the R3 parent keyed `('VWAP_LONG_R3', 239402)` has a future target fill at 18,648. Its prior completed close and current open are both 18,644.50; that minute later reaches 18,649. The old versus causal pre-entry ledger is:

| Ledger at the decision | Equity | Open reserve |
|---|---:|---:|
| Published settlement | $27,500.8673 | $0.00 |
| Keep the intraminute target unresolved | $27,497.6673 | $120.80 |

The development rerun finds one such queried future-fill event. No admission quantity changes; 1x net remains $9,355.85 and DD $1,908.80.

A development-dated synthetic simultaneous-order test establishes the more serious boundary failure: equity $22,500, mark/entry 100, stop 50, and an admitted two-contract R3 order reserve **$210.80**. A following one-contract order requires **$105.40**, so it must be rejected under the $225 cap. If the first order's simulated stop is stamped T, the current code prematurely books the loss, clears its reserve and **admits one**. The correct quantity is **zero**.

**Fix:** Track event availability separately from bar labels. Settle only information available before admission; retain newly admitted order reservations throughout the simultaneous-decision batch. Preserve the registered R3/TLB/short/re-entry order. The actual sorting/heap order is correct; it is the settlement of reservations that breaks it.

## 3. C6 loses a real remaining-lot STOP when two exit legs share a minute

**Location:** `sim.py:145-150`, `sim.py:190-197`, `sim.py:360`; incomplete protection in `audit.py:88-111`. **Severity: changes exit metadata and re-entry eligibility; no development P&L/verdict change for the three observed cases.**

`max(legs, key=timestamp)` returns the **first** leg on a timestamp tie. A cut at the minute open followed by a real protective stop later in that minute therefore reports `MGMT_CUT` as the final reason, and the cut price as the final exit. `reentry_for` then refuses even to search for a re-entry. The remaining lot's actual STOP must retain that right.

**Demonstration:** `python -B work/redteam_checks.py`.

| Parent ET entry date | Signal index | First leg: cut price | Later leg: STOP price | Reported final reason |
|---|---:|---:|---:|---|
| 2023-09-06 | 190172 | 15,452.75 | 15,438.75 | MGMT_CUT |
| 2023-10-25 | 199799 | 14,782.00 | 14,780.50 | MGMT_CUT |
| 2024-02-28 | 223910 | 17,984.00 | 17,982.00 | MGMT_CUT |

In each row both legs have the same timestamp. Using the last chronologically appended leg restores STOP and invokes the search. **None of these three searches finds a qualifying re-entry**, so C6's increment remains **+$15.50 at every cost multiplier**. Gross/net leg accounting itself was already correct. The supplied audit still says **29/29 actions verified**, illustrating that its checks do not test this linkage.

**Fix:** Use execution sequence to break timestamp ties, or explicitly expose the last appended terminal leg. Test cut-at-open/stop-later-in-minute, along with ordinary later-minute STOP and full management exit cases. Apply the correction to candidate and random-cut books alike.

## 4. C6's matched null often performs fewer cuts than the candidate

**Location:** `run_dev.py:174-190`, `run_dev.py:225-231`. **Severity: changes a number; C6 remains FAIL.**

The greedy without-replacement assignment silently drops an activation if earlier assignments exhaust its eligible parents, even after widening. The caller neither verifies the number selected nor the number actually filled. This violates matching one random action for every executed activation. Cancellation at the next open can reduce the count further.

**Demonstration:** `python -B work/redteam_nulls.py`, seed 1005, 200 development-only replicates. C6 has **29** actual cuts, but:

- Scheduled: 29 cuts in **67** replicates, 28 in **126**, 27 in **7**.
- Executed: 29 cuts in **65** replicates, 28 in **122**, 27 in **13**.
- Eight replicates execute fewer cuts than they schedule. For example replicate 5 schedules 28 and executes 27; key `('VWAP_LONG_R3', 213506)` does not fill its random cut.

Thus **135/200** comparisons execute fewer cuts than the candidate. In the same checks, C2, C3, C4, C8 and C9 execute their specified counts in all 200 replicates. C1 has zero actions in every replicate.

**Correction demonstration:** `python -B work/redteam_c6_null_fix.py` rejects incomplete assignments and unfilled schedules and also fixes the terminal-leg tie from finding 3. Producing 200 complete, 29-cut replicates takes **682 proposals**: 475 rejected for incomplete matching and seven for unfilled cuts. Candidate percentile is **56.5**, median null return/DD **4.107149**, p90 **4.306985**. C6 still has no support at the 90th-percentile gate. The unrepaired development-only percentile is 54.5; the published full-run percentile is 56.0.

**Fix:** Require complete matching and verify realized action counts. Use a randomized complete matching algorithm or a disclosed rejection scheme, with infeasibility reported rather than silently reducing interventions. Rejection here demonstrates a count-correct sensitivity; it does not establish a unique uniform distribution over feasible matchings.

## 5. The development runner does not computationally seal the holdout, and shares its RNG stream

**Location:** `sim.py:45-75`, `sim.py:78-106`; `run_dev.py:195-213`, `run_dev.py:223-247`, particularly `run_dev.py:245`. **Severity: changes development null numbers and violates computational isolation; no demonstrated verdict change.**

The loader computes indicators/signals over all supplied data. `run_dev` simulates all dates before masking daily results; it also writes unfiltered candidate trade frames. Its period-separated null pools do **not** isolate random streams: later-period activations/skip samples consume draws from the same RNG used by the next development replicate. Consequently development random samples depend on later-period processing even though the reported statistic is filtered.

**Demonstration:** `python -B work/redteam_nulls.py`, which uses exactly the registered seed per candidate but only development data:

| Candidate | Published percentile | Development-only percentile |
|---|---:|---:|
| C1 | 0.0 | 0.0 |
| C2 | 95.0 | **97.0** |
| C3 | 11.0 | 13.5 |
| C4 | 13.5 | 14.5 |
| C6 | 56.0 | 54.5 |
| C8 | 0.0 | 0.0 |
| C9 | 96.0 | **96.5** |

C2's p90 changes from $364.54 to $347.33; C9's from $321.30 to $362.38. In contrast, all nine candidate net/DD/bootstrap values exactly reproduce from the permitted prefix. `python -B work/redteam_stats.py` also demonstrates stream coupling with **synthetic RNG calls only**: inserting ten dummy calls between two otherwise identical C2 draws leaves only **2 of 11** selected parent keys shared in the next development draw. No later-period market observations are required for this demonstration.

This establishes computational dependence and a broken seal, **not evidence that economic holdout information was intentionally selected to improve results**. I did not calculate any holdout statistic to investigate it.

**Fix:** Enforce the date boundary in the input loader before indicators and simulation, guard terminal decisions, and give each period/candidate/replicate an independent reproducible random stream. Filter persisted trade artifacts before writing them during development. Merely filtering `series()` is insufficient for the user's restriction.

## 6. A pending VWAP cut can override mandatory flattening

**Location:** `sim.py:188-204`; compare the explicit guard already present in `sim.py:315`. **Severity: changes a number on a synthetic development-dated fixture; zero affected observed development activations.**

The VWAP loop executes a pending management order before checking the mandatory flatten window. Unlike the re-entry loop, it does not suppress that order when the next bar reaches the deadline. IDEAS.md's shared execution contract gives mandatory flattening precedence.

**Demonstration:** `python -B work/redteam_edges.py`. A two-lot long entered at 100 has a qualifying C6 decision at **2021-08-27 15:54 ET**. The 15:55 bar opens at 98 and closes at 101, with neither bracket touched. The existing loop cuts one at 98, flattens one at 101, and books **-$2 gross**. Giving mandatory flattening precedence flattens both at 101 under the control convention, for **+$4 gross**. The script applies the guard in memory and verifies the corrected legs. Costs are identical because both paths exit two contracts.

No actual development management fill falls in this window, and no actual activation has a delayed fill across a missing-bar gap. This is a confirmed latent execution bug, not a measured source of the reported C6 loss or gain.

**Fix:** Cancel pending discretionary orders at an existing mandatory deadline before applying them, while preserving the control's own stop/target/flatten priority.

## 7. The frozen verdict contains incorrect development diagnostics and omits C5 interventions

**Location:** `out/DEV_VERDICT.md:7`, `out/DEV_VERDICT.md:14`; `run_dev.py:310`, `audit.py:185-188`; original reporting requirements in `../ideas/out/IDEAS.md:113-115`. **Severity: changes reported numbers; no verdict change.**

**Demonstration:** `python -B work/redteam_edges.py`, `python -B work/redteam_stress.py`, and `python -B work/redteam_stats.py`.

- **C1:** Across the 260 development parents, median initial stop distance is **15.75 points**, not 18.5. **82** parents survive to an eligible completed bar-15 decision, not 125; **one**, not four, has `u <= -0.5` there. None also satisfies the MFE ceiling. Zero activations is correct. These are development-only counts; I did not inspect another period to identify the source of the erroneous counts.
- **C9:** The worst 10% of the registered all-ET-date control net curve improve by **$54.80**, rather than remaining unchanged. On **2022-01-30**, net changes from -$120.20 to -$94.80 (+$25.40); on **2022-01-31**, from -$113.80 to -$84.40 (+$29.40). The result is identical using 124 or 125 worst dates. The broader statement that tail insurance does not deteriorate survives, but the exact equality claim is false. The 61 rejected shorts, -$195 gross and 26/61 wins are correct.
- **C5:** The report's `acts=0` omits **63 changed admissions**: 50 TLB skips, nine re-entry skips and four R3 reductions from two contracts to one. `Log.admit` already contains them. The original requested utilization comparison is also absent. On the published 1x paths, total gross contract-minutes are **105,946 control / 97,392 C5 / 86,261 B1**, or **85.17 / 78.29 / 69.34** per registered ET date. Mean initial stop-risk dollars per admitted trade, excluding costs, are **$99.71 / $90.49 / $90.39** respectively.

**Fix:** Generate verdict diagnostics from the same development-only artifacts as the report; declare the all-day tail denominator. Report admission changes separately from management exits, and include the specified exposure measures. Narrow “no prize exists” to the tested fixed rule's zero activations.

## 8. Two explicit PREREG departures materially weaken the original IDEAS validation contract

**Location:** `PREREG.md:44-45`, `PREREG.md:68-71`, `run_dev.py:254-264`; versus `../ideas/out/IDEAS.md:19-23`. **Severity: changes a verdict if the original IDEAS contract is enforced; not an implementation deviation from the frozen PREREG.**

### A. The 24-month history requirement was removed

IDEAS specifies a 24-month training/history window followed by six-month test windows. PREREG explicitly waives that window on the grounds that no parameter is fitted. That is a disclosed methodological change, not exact implementation of the original validation contract.

**Demonstration:** `python -B work/redteam_stress.py`. Source history starts **2020-12-30**, so 24 calendar months are available on **2022-12-30**. Without inventing new fold boundaries, retain only the five registered complete folds starting **2023-02-27** or later:

| Candidate | Retained incremental net, 1x | Positive folds | Drop-top-5 increment |
|---|---:|---:|---:|
| C2 | +$291.18 | 3/5 | **-$38.12** |
| C9 | +$116.70 | 3/5 | **-$17.60** |

Both fail the original concentration condition despite having a majority of positive folds. This is a conservative alignment to existing complete folds, not a claim that IDEAS specified a unique calendar anchor. No thresholds, sleeves or entries were changed.

### B. Holm is applied to bootstrap support, not the matched random null

IDEAS requires support against a **family-adjusted permutation null**. PREREG instead family-adjusts the paired day-bootstrap tail probabilities and separately accepts an unadjusted 90th-percentile random-trigger rank. Those are different questions: positive incremental returns versus information beyond matched random intervention. Passing the adjusted former does not imply passing the adjusted latter.

**Demonstration:** `python -B work/redteam_nulls.py`. From the 200 development-only matched-null replicates, the plus-one upper-tail exceedance is **7/201 = 0.03483 for C2** and **8/201 = 0.03980 for C9**. Applying Holm over the same nine counted variants, with p=1 for the two without matched nulls, gives **0.31343** and **0.31841**. Both fail 0.10. These are matched-randomization diagnostic p-values; I do not assert an exact exchangeability theorem for the heuristic matching scheme.

**Fix:** Describe the frozen result accurately as “PASS under PREREG's revised validation contract.” If the original IDEAS requirements govern promotion, record the discrepancy and the corrected eligibility explicitly: neither C2 nor C9 qualifies in these demonstrations. Do not silently replace the preregistered gate after seeing results. I have not edited PREREG or run any new trading-rule variant.

## Checks that passed and limits of the inference

`redteam_checks.py` reproduces all **1,119** development control trades exactly against the verified engine: **260 R3 parents, 200 VWAP shorts, 537 TLB trades and 122 re-entries**, including times, prices and reasons. Record mode leaves the book unchanged. Every candidate's 1x/2x/3x net increments, 1x/2x DD, drop-top-5 and raw bootstrap probability match `dev_results.json`; all nine Holm values match.

The available-data implementation of C1/C2/C3/C4/C6/C8 has the specified thresholds, scopes and decision timing. The independent audit verifies **0/11/89/6/29/1** executed actions respectively. C3 freezes the entry pivots and uses two post-entry closes; MFE includes completed post-entry observations. C4 retains its initial target type and two-check condition; the original UTC-session exit prevents its state crossing a reset. C6's 10/60 disjoint semivariance slices and >30-minute gap mask agree with independent re-derivation. C8 uses RMS of 60 increments, not centered standard deviation. C9 uses the latest completed minute close, the strict 13.5-point floor, and unchanged baseline fills. Its admitted trades and all 64 skipped signals have no planned/fill-risk mismatch in this book. The source schema is only time/OHLC/volume, so this audit verifies PREREG's gap proxy, not independent contract identity across every C6 lookback required literally by IDEAS.

Earlier management exits release the base-sleeve busy state and the whole path is recomputed. Full management exits do not create descendants. Remaining-lot STOP linkage has the timestamp-tie exception in finding 3. Control gap-through-stop/target cancellation checks return the expected results; management fills occur at the next available minute open in the audited data.

Each trade's exited leg quantities sum to its admitted quantity. At every stress, leg net equals gross minus **$5.40 x multiplier x quantity** exactly within floating-point tolerance: partial exits do not double-charge costs. C5's problem is the equity used for later sizing, not the final per-leg fee arithmetic.

The daily calendar contains **1,244 observed ET dates**, including zero-trade dates. The eight folds have **156, 155, 155, 155, 156, 155, 158, 154** dates and partition the calendar exactly. Scalar independent DD and independently sorted paired day-removal calculations agree for all candidates. The stationary bootstrap uses paired circular continuations with geometric restarts and the specified seed, 5,000 replicates and mean block 10. Its observed non-continuation rate is **0.099953**, versus expected **0.099920** after accidental continuation on a fresh draw. A separate Holm toy check confirms monotone step-down adjustment. These checks validate implementation of the declared statistics, not the original contract changes in finding 8.

C2's exact frozen increment is **+$542.0831**, drop-top-5 **+$4.3831**, raw bootstrap p **0.0046**, Holm **0.0368**. Its 11 interventions comprise nine R3 parents and two shorts, all STOPs in control, spread across several UTC hours. C9's corresponding values are **+$514.10**, **+$325.00**, **0.0008**, and **0.0072**. The tiny C2 concentration margin is real; I did not add hypothetical extra fees or tune an alternative rule to force it negative.

C1 and C8 have degenerate matched-null distributions here: no actions for C1 and one exact clock-matched choice for C8. Their strict-below ranks of zero should not be read as precise evidence of negative information. C6's failure of this test likewise supports “no demonstrated advantage over random cuts,” not proof of no information. Nothing in this report supplies holdout evidence or promotes any failed candidate.

# Development verdict — frozen 2026-09-14, before the holdout was opened

Source: out/DEV_REPORT.md (2021-08-27 .. 2025-08-26; control dev net $11,357, max DD $2,893 at 1x).

| id | idea | verdict | why |
|---|---|---|---|
| C1 | failed-rebound early abort | FAIL (inert) | 0 dev activations. R3 parents have tight stops (median 18.5pt); of 125 still open at bar 15 only 4 are <= -0.5R. No prize exists on this book. |
| C2 | moving-VWAP invalidation | **PASS** | +$542 1x / +$574 2x / +$607 3x; 5/8 folds; Holm p 0.037; null pct 95. All 11 exited trades were full STOPs in the control. Caveats: only 11 activations in 4 yrs; drop-top-5 leaves +$4 (passes the letter of P3, not its spirit). |
| C3 | TLB line failure | FAIL | -$2,491; 1/8 folds; cuts TLB winners that retest the line (forgone -$5,908 vs saved +$3,440). |
| C4 | re-entry reward collapse | FAIL | -$200, 6 activations, all worse. |
| C5 | 1% gross stop-risk ceiling | FAIL | DD 2,893 -> 1,909 and ret/DD 4.90 vs 3.93, but keeps only 82% of net (R2 needs 95%); drop-top-5 fails. It is a deleveraging choice, not an edge. |
| C6 | volatility-burst cut 1 lot | FAIL | +$16, null pct 56 — no information beyond a random cut. |
| C8 | close-time feasibility | FAIL (inert) | 1 dev activation (-$84). |
| C9 | 13.5pt stop floor on 1R sleeves | **PASS** | +$514 1x / +$860 2x / +$1,205 3x; 6/8 folds (2 zero); drop-top-5 +$325; Holm p 0.007; null pct 96. Acts almost only on VWAP_SHORT_R1 (61 of 64 skips): those shorts were gross -$195, win 43%. Control's worst-10% days unchanged (insurance intact). |
| B1 | R3 at 1 lot | FAIL | keeps 78% of net. |

Eligible for the holdout gate: **C2, C9**. Nothing else may be promoted regardless of holdout.
Holdout criteria (from PREREG): Profit — increment > 0 at 1x AND 2x on 2025-08-27 .. 2026-08-25.
Even if both pass, the next step is paper/forward monitoring, not live, and the combination
C2+C9 would be a new counted variant (#10 in the family) requiring its own addendum.

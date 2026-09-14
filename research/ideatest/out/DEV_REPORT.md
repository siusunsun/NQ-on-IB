# Codex ideas — DEVELOPMENT stage results (2021-08-27 .. 2025-08-26)

Holdout (2025-08-27 .. 2026-08-25) is sealed and not in any number below.

Control dev: net 1x $11,357, 2x $3,911, 3x $-3,536; max DD 1x $2,893; ret/DD 3.93

| id | obj | acts | Δnet 1x | Δnet 2x | Δnet 3x | DD ctrl→cand 1x | folds + | drop5 Δ | boot p | Holm | null pct | DEV |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|
| C1 | Risk | 0 | +0 | +0 | +0 | 2,893→2,893 | 0/8 | +0 | 1.000 | 1.000 | 0 | fail |
| C2 | Profit | 11 | +542 | +574 | +607 | 2,893→2,764 | 5/8 | +4 | 0.005 | 0.037 | 95 | PASS |
| C3 | Profit | 89 | -2,491 | -2,594 | -2,696 | 2,893→2,505 | 1/8 | -3,631 | 0.985 | 1.000 | 11 | fail |
| C4 | Profit | 6 | -200 | -200 | -200 | 2,893→2,893 | 0/8 | -200 | 1.000 | 1.000 | 14 | fail |
| C5 | Risk | 0 | -2,001 | -1,726 | -1,451 | 2,893→1,909 | 5/8 | -3,810 | 0.268 | 1.000 | nan | fail |
| C6 | Risk | 29 | +16 | +16 | +15 | 2,893→2,751 | 5/8 | -389 | 0.224 | 1.000 | 56 | fail |
| C8 | Risk | 1 | -84 | -84 | -84 | 2,893→2,893 | 0/8 | -84 | 0.972 | 1.000 | 0 | fail |
| C9 | Profit | 64 | +514 | +860 | +1,205 | 2,893→2,893 | 6/8 | +325 | 0.001 | 0.007 | 96 | PASS |
| B1 | Risk | 0 | -2,481 | -1,077 | +328 | 2,893→2,367 | 7/8 | -3,234 | 0.088 | 0.617 | nan | fail |

Folds + = folds with positive Δnet (Profit) or strictly lower fold DD (Risk).

## C1 (Risk)
- criteria: R1 DD lower 1x,2x ✗; R2 net>=95% 1x,2x ✓; R3 fold DD lower >=5/8 ✗; R4 drop-top-5 R1,R2 hold ✗; R6 null >=90th pct ✗; P6 3x DD still lower ✗; R5 Holm p<=0.10 ✗
- fold Δnet: +0, +0, +0, +0, +0, +0, +0, +0
- fold DD ctrl/cand: 786/786, 764/764, 519/519, 1881/1881, 690/690, 2589/2589, 572/572, 910/910
- net cand 1x/2x/3x: 11,357 / 3,911 / -3,536; DD cand 2x 3,831 vs ctrl 3,831; ret/DD 3.93 vs 3.93
- decomposition: 0 trades changed (saved +0, forgone +0); lost R3 re-entries 0 (+0); other trades lost 0 (+0); new trades 0 (+0)
- null: median +3.93, p90 +3.93, candidate pct 0

## C2 (Profit)
- criteria: P1 delta>0 at 1x,2x ✓; P2 >=5/8 folds positive ✓; P3 drop-top-5 still >0 ✓; P5 null >=90th pct ✓; P6 3x not negative ✓; P4 Holm p<=0.10 ✓
- fold Δnet: +212, +38, +0, +28, -15, +129, +0, +149
- fold DD ctrl/cand: 786/685, 764/764, 519/519, 1881/1881, 690/690, 2589/2460, 572/572, 910/887
- net cand 1x/2x/3x: 11,899 / 4,485 / -2,929; DD cand 2x 3,712 vs ctrl 3,831; ret/DD 4.31 vs 3.93
- decomposition: 11 trades changed (saved +496, forgone +0); lost R3 re-entries 6 (+47); other trades lost 0 (+0); new trades 0 (+0)
- null: median -188.92, p90 +364.54, candidate pct 95

## C3 (Profit)
- criteria: P1 delta>0 at 1x,2x ✗; P2 >=5/8 folds positive ✗; P3 drop-top-5 still >0 ✗; P5 null >=90th pct ✗; P6 3x not negative ✗; P4 Holm p<=0.10 ✗
- fold Δnet: -75, -892, -637, -415, +41, -78, -243, -191
- fold DD ctrl/cand: 786/894, 764/643, 519/658, 1881/1840, 690/588, 2589/2343, 572/576, 910/999
- net cand 1x/2x/3x: 8,866 / 1,317 / -6,232; DD cand 2x 4,392 vs ctrl 3,831; ret/DD 3.54 vs 3.93
- decomposition: 85 trades changed (saved +3,440, forgone -5,908); lost R3 re-entries 0 (+0); other trades lost 0 (+0); new trades 19 (-23)
- null: median -1,515.00, p90 -211.72, candidate pct 11

## C4 (Profit)
- criteria: P1 delta>0 at 1x,2x ✗; P2 >=5/8 folds positive ✗; P3 drop-top-5 still >0 ✗; P5 null >=90th pct ✗; P6 3x not negative ✗; P4 Holm p<=0.10 ✗
- fold Δnet: +0, +0, +0, -23, +0, -16, -161, +0
- fold DD ctrl/cand: 786/786, 764/764, 519/519, 1881/1881, 690/690, 2589/2589, 572/572, 910/910
- net cand 1x/2x/3x: 11,157 / 3,710 / -3,736; DD cand 2x 3,847 vs ctrl 3,831; ret/DD 3.86 vs 3.93
- decomposition: 6 trades changed (saved +0, forgone -200); lost R3 re-entries 0 (+0); other trades lost 0 (+0); new trades 0 (+0)
- null: median -74.10, p90 +62.74, candidate pct 14

## C5 (Risk)
- criteria: R1 DD lower 1x,2x ✓; R2 net>=95% 1x,2x ✗; R3 fold DD lower >=5/8 ✓; R4 drop-top-5 R1,R2 hold ✗; R6b beats B1 ret/DD ✓; P6 3x DD still lower ✓; R5 Holm p<=0.10 ✗
- fold Δnet: +228, -502, -331, +284, -333, +517, -2242, +379
- fold DD ctrl/cand: 786/463, 764/419, 519/592, 1881/1456, 690/690, 2589/1783, 572/1030, 910/910
- net cand 1x/2x/3x: 9,356 / 2,185 / -4,987; DD cand 2x 3,011 vs ctrl 3,831; ret/DD 4.90 vs 3.93
- decomposition: 4 trades changed (saved +274, forgone -283); lost R3 re-entries 9 (-437); other trades lost 44 (-1,338); new trades 6 (-218)

## C6 (Risk)
- criteria: R1 DD lower 1x,2x ✗; R2 net>=95% 1x,2x ✓; R3 fold DD lower >=5/8 ✓; R4 drop-top-5 R1,R2 hold ✗; R6 null >=90th pct ✗; R6b beats B1 ret/DD ✓; P6 3x DD still lower ✗; R5 Holm p<=0.10 ✗
- fold Δnet: +85, +21, +18, -196, -96, +22, +98, +62
- fold DD ctrl/cand: 786/723, 764/764, 519/519, 1881/1847, 690/690, 2589/2448, 572/572, 910/893
- net cand 1x/2x/3x: 11,373 / 3,926 / -3,520; DD cand 2x 3,871 vs ctrl 3,831; ret/DD 4.13 vs 3.93
- decomposition: 29 trades changed (saved +772, forgone -756); lost R3 re-entries 0 (+0); other trades lost 0 (+0); new trades 0 (+0)
- null: median +4.09, p90 +4.34, candidate pct 56

## C8 (Risk)
- criteria: R1 DD lower 1x,2x ✗; R2 net>=95% 1x,2x ✓; R3 fold DD lower >=5/8 ✗; R4 drop-top-5 R1,R2 hold ✗; R6 null >=90th pct ✗; P6 3x DD still lower ✗; R5 Holm p<=0.10 ✗
- fold Δnet: +0, +0, +0, -84, +0, +0, +0, +0
- fold DD ctrl/cand: 786/786, 764/764, 519/519, 1881/1881, 690/690, 2589/2589, 572/572, 910/910
- net cand 1x/2x/3x: 11,273 / 3,827 / -3,620; DD cand 2x 3,831 vs ctrl 3,831; ret/DD 3.90 vs 3.93
- decomposition: 1 trades changed (saved +0, forgone -84); lost R3 re-entries 0 (+0); other trades lost 0 (+0); new trades 0 (+0)
- null: median +3.90, p90 +3.90, candidate pct 0

## C9 (Profit)
- criteria: P1 delta>0 at 1x,2x ✓; P2 >=5/8 folds positive ✓; P3 drop-top-5 still >0 ✓; P5 null >=90th pct ✓; P6 3x not negative ✓; P4 Holm p<=0.10 ✓
- fold Δnet: +128, +27, +242, +0, +16, +0, +23, +78
- fold DD ctrl/cand: 786/786, 764/764, 519/458, 1881/1881, 690/690, 2589/2589, 572/572, 910/910
- net cand 1x/2x/3x: 11,871 / 4,770 / -2,331; DD cand 2x 3,778 vs ctrl 3,831; ret/DD 4.10 vs 3.93
- decomposition: 0 trades changed (saved +0, forgone +0); lost R3 re-entries 0 (+0); other trades lost 64 (+514); new trades 0 (+0)
- null: median -217.80, p90 +321.30, candidate pct 96

## B1 (Risk)
- criteria: R1 DD lower 1x,2x ✓; R2 net>=95% 1x,2x ✗; R3 fold DD lower >=5/8 ✓; R4 drop-top-5 R1,R2 hold ✗; P6 3x DD still lower ✓; R5 Holm p<=0.10 ✗
- fold Δnet: -220, +74, +172, +224, -817, +264, -1762, -416
- fold DD ctrl/cand: 786/657, 764/764, 519/487, 1881/1576, 690/690, 2589/2042, 572/532, 910/810
- net cand 1x/2x/3x: 8,877 / 2,834 / -3,208; DD cand 2x 3,631 vs ctrl 3,831; ret/DD 3.75 vs 3.93
- decomposition: 260 trades changed (saved +7,138, forgone -9,618); lost R3 re-entries 0 (+0); other trades lost 0 (+0); new trades 0 (+0)

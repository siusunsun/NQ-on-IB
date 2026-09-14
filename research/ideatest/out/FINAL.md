# Codex ideas — final result (2026-09-14)

Protocol: PREREG.md (frozen before any run; addendum records the Codex harness red team).
Control book: dev 2021-08-27..2025-08-26 net $11,357 / DD $2,893; holdout 2025-08-27..2026-08-25 net $7,332 / DD $2,344 (1x cost $5.40/contract RT).

| id | idea | dev | holdout Δ 1x / 2x | status |
|---|---|---|---|---|
| C2 | exit when session VWAP keeps moving against a losing VWAP trade | PASS +$542 (11 acts) | +$91 / +$101 (4 acts) | passes the registered gates; weak evidence |
| C9 | skip 1R trades whose stop < 13.5pt (in practice: thin VWAP shorts) | PASS +$514 (64 skips) | +$3 / +$19 (3 skips) | passes the registered gates; holdout ≈ zero |
| C1 | early abort (HSI-style) | 0 dev acts | -$160 | fail — inert on NQ |
| C3 | TLB line-failure exit | -$2,491 | +$464 | fail (dev); holdout flip = noise, not eligible |
| C4 | re-entry reward collapse | -$200 | -$196 | fail |
| C5 | 1% gross risk ceiling | -$2,001 (DD -34%) | -$2,701 | fail — deleveraging, keeps ~80% of net |
| C6 | cut 1 lot on volatility burst | +$16 (null 56) | -$1,078 | fail |
| C8 | close-time feasibility exit | 1 act | $0 | fail — inert |
| C7 | flatten before CPI/FOMC (added later, own addendum) | -$677 (4 acts) | not run | fail — all 4 cut trades hit TARGET in the release minute |
| B1 | R3 parent 1 lot | -$2,481 | -$2,357 | fail |

Why C2/C9 are not "validated": they fail Codex's original stricter contract (family-adjusted random
null Holm p ≈ 0.31; post-24-month drop-top-5 negative), each acts only ~3 times a year, and C9's
holdout effect is +$3. Mechanisms are plausible (C2: all 11 dev exits were trades the control lost
at the full stop; C9: thin shorts lose even before costs), and neither increased drawdown anywhere.

Recommendation: no live change. If wanted, shadow-log both in the live V9 bot (log only — no
orders) and review when ≥ 10 forward activations exist. C2+C9 together is an untested, counted variant.
#7 tested after the fact with official BLS/Fed dates (events/events.csv): fails. #11 not bought — NQ-side ceiling check shows exiting every eligible trade loses $2,942, TLB near coin-flip. #10 dropped (MNQ-only execution idea).

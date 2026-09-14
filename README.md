# NQ-on-IB — V9 regime-gated MNQ book + R3 re-entry (PRIVATE)

Live on Interactive Brokers (account-guarded), trading **MNQ** off **NQ** signals, on the Hetzner
VPS `/root/v9` and `/root/r3re_live`. The VPS is not a git checkout — this repo is synced by copying
the live files in (see *Sync* below).

## What runs live

| Process | Dir | clientId | What it does |
|---|---|---|---|
| V9 (`v9_run_user.py` → `v9_live_trade.py`) | `v9/` | 25 | Daily regime from the prior close (S200, R20) → sleeves: VWAP_LONG_R3 ×2, VWAP_SHORT_R1 ×1, TLB_LONG ×1 (hybrid-40 stop). 5-min bars, market entries, server-side OCA brackets, 15:55 ET flatten. |
| R3 re-entry (`r3re_live_bot.py`) | `r3re_live/` | 65 | After a VWAP_LONG_R3 stop-out: a resting BUY-STOP (DAY) at the post-stop high, modified in place as it rises, for up to 48 bars; on fill an OCA stop (failed-move low) + limit target (session VWAP / +2σ) that follows the magnet in place. |

Both are respawned by cron watchdogs (`ops/crontab.txt`). Telegram: every NQ message is
`[NQ-<LEVEL>] [V9] ...` or `[NQ-<LEVEL>] [R3_REENTRY] ...`.

## Layout

- `v9/` — live bot, config (`v9_config_live_user.py` is the live one), contract roll / expiry
  calendar (`contract_roll.py`), data puller (`tools/`), Telegram (`v9_tg.py`).
- `v9/bt/` — backtest harness and research scripts (`bt_nq.py`, `_hist_bt_nq.py`), dashboard push.
- `r3re_live/` — R3 re-entry executor, its spec (`r3v_live_spec.py`), live→batch parity test
  (`_parity.py`, 25/25) and the old-code control (`_parity_old.py`).
- `tests/` — offline suites, no IB connection and no orders: `_test_v9.py` (35), `_test_r3_stage.py` (43).
  They import from `/root/_stage/...` first, then the live dirs.
- `research/` — `repro/`: an independent re-implementation from a written spec (matches the harness
  trade-for-trade); `ideatest/`: the pre-registered test of Codex's 11 ideas (all rejected for live,
  see `research/ideatest/out/FINAL.md`); `redteam/`: the R3 re-entry red team.
- `ops/crontab.txt` — the NQ cron lines.

## Not in the repo (by design)

Secrets are read from files on the VPS only: Polygon key `/root/v9/.polygon_key`, Telegram via
`/root/hsi/alert.sh` + `/root/hsi/.alert_config`. Also excluded: logs, state/ledger JSON, the 1-min
data archive (licensed data), `*.bak*` backups.

## Sync (VPS → repo)

```
ssh root@178.105.116.241 'cd /root && tar czf /tmp/nq_repo.tgz --exclude="*.bak*" --exclude="*bak_*" \
  --exclude="__pycache__" --exclude="*.log" --exclude="*.json" --exclude="*.csv" v9/*.py v9/*.sh \
  v9/src/engine.py v9/tools/*.py v9/bt/*.py v9/bt/*.txt r3re_live/*.py r3re_live/*.sh \
  _stage/_test_v9.py _stage/_test_r3_stage.py'
```
Unpack over the clone, secret-scan, commit, push, then md5-compare `git show HEAD:<file>` with the VPS.
Always diff repo vs VPS before deploying FROM the repo — the VPS is the source of truth.

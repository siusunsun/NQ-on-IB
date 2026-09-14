"""Standalone nightly regime-cache refresh — runs while V9 MNQ bot is PAUSED.

A1 (0DTE options bot) gates on /root/v9/v9_live_regime_cache.json but does not
compute regime itself. V9 used to refresh this cache at daily setup; with V9
paused, this script keeps it fresh.

Pipeline: pull 1-min data (Polygon, completeness-checked, IB fallback) ->
aggregate daily closes -> compute regime -> write cache. Respects V9 lock
semantics: same prior-day date -> only overwrite if the close differs (data
completed later); logs any cell change loudly.
"""
import sys, json, logging
from datetime import datetime, timezone

sys.path.insert(0, "/root/v9")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s UTC [%(levelname)s] %(message)s")
log = logging.getLogger("regime_refresh")

from tools.v9_pull_latest_data import run as pull_run
from v9_live_trade import load_daily_closes_for_regime
from v9_strategy_lib import compute_regime
import v9_config_live_user as C


def main():
    pull_result = pull_run()
    log.info("Data pull: %s", pull_result)
    if not str(pull_result).startswith("OK"):
        log.error("Data pull FAILED (%s) — NOT touching the regime cache. "
                  "A1 keeps the locked cache; its staleness guard stops trading "
                  "if this persists past 3 days.", pull_result)
        return
    closes, counts = load_daily_closes_for_regime()
    reg = compute_regime(closes, C.REGIME["sma_window"], C.REGIME["momentum_window"])
    log.info("Computed: date_et=%s close=%.2f ret20=%+.2f%% cell=%s",
             reg.date_et, reg.close, reg.ret20 * 100, reg.cell)

    try:
        with open(C.REGIME_CACHE_FILE) as f:
            old = json.load(f)
    except Exception:
        old = None

    if old and old.get("date_et") and str(old["date_et"]) > str(reg.date_et):
        log.error("Computed prior-day %s is OLDER than cached %s — data is stale, "
                  "refusing to regress the cache.", reg.date_et, old["date_et"])
        return
    if old and old.get("date_et") == reg.date_et and abs(old.get("close", 0) - reg.close) < 0.01:
        log.info("Cache already current (same date, same close) — no write.")
        return
    if old and old.get("date_et") == reg.date_et:
        log.warning("Same prior-day %s but close changed %.2f -> %.2f (late data) — overwriting.",
                    reg.date_et, old.get("close", 0), reg.close)

    with open(C.REGIME_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(dict(date_et=reg.date_et, close=reg.close, sma200=reg.sma200,
                       ret20=reg.ret20, s200=reg.s200, r20=reg.r20,
                       computed_at_utc=datetime.now(timezone.utc).isoformat()), f, indent=2)
    log.info("Cache written: %s cell=%s", C.REGIME_CACHE_FILE, reg.cell)


if __name__ == "__main__":
    main()

"""Telegram alert helper for V9 — uses the existing HSI alert.sh infrastructure.

Forwards to Telegram:
- ALL WARNING / ERROR / CRITICAL log records
- INFO records that contain any of the IMPORTANT_KEYWORDS (signals, fills, flatten, etc.)
"""
import logging
import re
import subprocess
import threading
import time

ALERT_SH = "/root/hsi/alert.sh"
IMPORTANT_KEYWORDS = (
    "SIGNAL", "DAILY SETUP", "ENTRY", "EXIT", "FILL",
    "flatten", "FLATTEN", "EOD", "halt", "HALT",
    "DAILY-LOSS", "REGIME CLOSE", "ARMED",
)

_lock = threading.Lock()
_last_sent: dict[str, float] = {}
_DEDUP_SEC = 90


def send(msg: str, prefix: str = "NQ", dedup_key: str | None = None) -> None:
    """Fire-and-forget Telegram alert. Does NOT block."""
    full = f"[{prefix}] {msg}"
    if dedup_key is not None:
        now = time.time()
        with _lock:
            if dedup_key in _last_sent and now - _last_sent[dedup_key] < _DEDUP_SEC:
                return
            _last_sent[dedup_key] = now
    try:
        subprocess.Popen([ALERT_SH, full],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        pass


class TGLogHandler(logging.Handler):
    """Pipe WARNING+ and important-INFO log records to Telegram, with dedup."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self._pat = re.compile("|".join(re.escape(k) for k in IMPORTANT_KEYWORDS),
                               re.IGNORECASE)

    def emit(self, record):
        try:
            level = record.levelno
            msg = record.getMessage()
            # ALWAYS forward WARNING+, plus INFO that contains an important keyword
            if level >= logging.WARNING or self._pat.search(msg):
                # one NQ channel for every NQ bot; the bot is named inside: [NQ-INFO] [V9] ...
                # (R3 re-entry sends [NQ-INFO] [R3_REENTRY] ...)
                send(f"[V9] {msg[:350]}", prefix=f"NQ-{record.levelname}",
                     dedup_key=msg[:80])
        except Exception:
            pass


def install_handler(logger: logging.Logger) -> None:
    """Attach a TG handler to the bot's logger."""
    h = TGLogHandler()
    h.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(h)

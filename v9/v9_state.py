"""Persistent per-day state for the v9.1 paper bot.

JSON heartbeat written every minute so the bot can resume after a crash mid-session.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict


@dataclass
class SleeveState:
    """Per-sleeve runtime state."""
    name: str                              # 'TLB_LONG', 'VWAP_LONG_R3', 'VWAP_SHORT_R1'
    active_today: bool = False             # set at session start based on regime cell
    in_position: bool = False
    side: Optional[str] = None             # 'long' / 'short' / None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    entry_time: Optional[str] = None       # ISO ET
    entry_order_id: Optional[int] = None
    stop_order_id: Optional[int] = None
    target_order_id: Optional[int] = None
    realized_R: float = 0.0
    trade_count_today: int = 0
    qty: int = 0                           # actual contracts held (0 when flat)
    entry_session: Optional[str] = None    # TBS: which session (PRE/OPEN/MIDDAY) entered in


@dataclass
class BotState:
    """Top-level state container, serialized to JSON for crash recovery."""
    date_et: str                                          # YYYY-MM-DD
    regime_cell: str = ""                                 # BOTH_BULL / ZONE_A / ZONE_B / BOTH_BEAR
    regime_close: float = 0.0
    regime_sma200: float = 0.0
    regime_ret20: float = 0.0
    sleeves: Dict[str, SleeveState] = field(default_factory=dict)
    cum_realized_usd: float = 0.0
    halted: bool = False                                  # daily-loss-limit hit
    last_heartbeat: Optional[str] = None
    connected: bool = True                                # IB API socket connected at last heartbeat

    # ---- serialization ----
    def to_json(self) -> str:
        return json.dumps({
            "date_et": self.date_et,
            "regime_cell": self.regime_cell,
            "regime_close": self.regime_close,
            "regime_sma200": self.regime_sma200,
            "regime_ret20": self.regime_ret20,
            "sleeves": {n: asdict(s) for n, s in self.sleeves.items()},
            "cum_realized_usd": self.cum_realized_usd,
            "halted": self.halted,
            "last_heartbeat": self.last_heartbeat,
            "connected": self.connected,
        }, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "BotState":
        d = json.loads(raw)
        st = cls(date_et=d["date_et"])
        st.regime_cell    = d.get("regime_cell", "")
        st.regime_close   = d.get("regime_close", 0.0)
        st.regime_sma200  = d.get("regime_sma200", 0.0)
        st.regime_ret20   = d.get("regime_ret20", 0.0)
        st.cum_realized_usd = d.get("cum_realized_usd", 0.0)
        st.halted         = d.get("halted", False)
        st.last_heartbeat = d.get("last_heartbeat")
        st.connected      = d.get("connected", True)
        for n, p in (d.get("sleeves") or {}).items():
            st.sleeves[n] = SleeveState(**p)
        return st

    def save(self, path: str | Path) -> None:
        self.last_heartbeat = datetime.now(timezone.utc).isoformat()
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load_or_new(cls, path: str | Path, today_et: str,
                    sleeve_names: list[str]) -> "BotState":
        p = Path(path)
        if p.exists():
            try:
                st = cls.from_json(p.read_text(encoding="utf-8"))
                if st.date_et == today_et:
                    for n in sleeve_names:
                        st.sleeves.setdefault(n, SleeveState(name=n))
                    return st
                # Date rolled over (possibly while the bot was down). Carry any sleeve
                # still HOLDING a position forward instead of forgetting it — VWAP
                # sleeves may hold to their server-side bracket across midnight ET.
                st.roll_to_new_day(today_et, sleeve_names)
                return st
            except Exception:
                pass
        return cls(
            date_et=today_et,
            sleeves={n: SleeveState(name=n) for n in sleeve_names},
        )

    def reset_day(self, today_et: str, sleeve_names: list[str]) -> None:
        """Hard reset — wipes ALL sleeves. Only safe when known flat; the normal
        overnight rollover goes through roll_to_new_day (which preserves open holds)."""
        self.date_et = today_et
        self.regime_cell = ""
        self.cum_realized_usd = 0.0
        self.halted = False
        self.sleeves = {n: SleeveState(name=n) for n in sleeve_names}

    def roll_to_new_day(self, today_et: str, sleeve_names: list[str]) -> None:
        """Start a new ET day. Daily counters reset, but any sleeve still HOLDING a
        position is carried forward unchanged (VWAP sleeves run to their server-side
        bracket and may span the midnight-ET boundary) so the bot keeps tracking the
        open position and never stacks a second entry on top of it."""
        self.date_et = today_et
        self.regime_cell = ""
        self.regime_close = 0.0
        self.regime_sma200 = 0.0
        self.regime_ret20 = 0.0
        self.cum_realized_usd = 0.0
        self.halted = False
        rolled: Dict[str, SleeveState] = {}
        for n in sleeve_names:
            old = self.sleeves.get(n)
            if old is not None and old.in_position:
                old.active_today = False     # re-set by setup_for_day from today's regime
                old.realized_R = 0.0         # daily metric; the open position is carried
                old.trade_count_today = 0
                rolled[n] = old              # carry the open position forward
            else:
                rolled[n] = SleeveState(name=n)
        self.sleeves = rolled

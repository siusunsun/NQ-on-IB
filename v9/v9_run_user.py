"""LIVE launcher for V9 on the user's VPS. Loads v9_config_live_user.py."""
import sys
import os
sys.path.insert(0, "/root/v9")

import v9_config_live_user as cfg
sys.modules["v9_config"] = cfg

import v9_live_trade  # safety_check fires at import; raises if config bad

v9_live_trade.ENABLE_LONG = cfg.ENABLE_LONG
v9_live_trade.ENABLE_SHORT = cfg.ENABLE_SHORT

if __name__ == "__main__":
    mode = "ARMED (real orders)" if (cfg.ENABLE_LONG or cfg.ENABLE_SHORT) else "DRY RUN (no orders)"
    print(f"V9 LIVE launcher — account {cfg.LIVE_ACCOUNT}  port {cfg.IB_PORT}  clientId {cfg.CLIENT_ID}")
    print(f"Mode: {mode}")
    print()
    v9_live_trade.main()

"""Shared NQ/MNQ-data Gateway FAILOVER for the v9 + ORB bots and the NQ->MT5 bridge.

WHY: on 2026-06-11 the DUQ621725 feed went dark all day (paper market-data sharing was off) and both
bots sat blind. The two paper Gateways draw CME data from INDEPENDENT live accounts, so the secondary
is a TRUE fallback (different point of failure), not the same feed:
    PRIMARY   port 4003  DUQ621725  (data shared from sing0906 / U3386288)
    FAILOVER  port 4002  DU4234078  (data shared from sing09061982 / U6622554)
Both Gateways must keep auto-starting (launch_ibc_orb_hidden.vbs + launch_ibc_hidden.vbs).

Two pieces (the per-bot run loops wire these in):
  connect_failover(...) — at startup, try primary then secondary until one connects with a valid
                          PAPER account ('DU' prefix); retry the whole list FOREVER (a waiting bot
                          beats a dead one — any open position rides its server-side GTC bracket).
  other_port(current)   — the port to switch to when the CURRENT feed idles (bar-stall escalates in
                          RTH). The caller: disconnect -> connect to other_port -> re-resolve contract
                          -> re-subscribe bars. (Switch only on the >10-min ESCALATION, not every
                          stall, so a brief blip can't flap between Gateways.)
"""
from __future__ import annotations

import time

DATA_GATEWAYS = [4003, 4002]   # 4003 DUQ621725 (primary), 4002 DU4234078 (independent-feed failover)


def other_port(current: int, ports=DATA_GATEWAYS) -> int:
    """The next Gateway port to fail over to (cycles through the list)."""
    if current not in ports:
        return ports[0]
    return ports[(ports.index(current) + 1) % len(ports)]


def connect_failover(ib, host: str, client_id: int, account_prefix: str, log,
                     ports=DATA_GATEWAYS, timeout: int = 15, sweep_sleep: float = 60.0) -> int:
    """Connect `ib` to the first reachable Gateway in `ports` whose account matches `account_prefix`
    (paper-only safety). Returns the connected port. Retries the FULL list forever; logs the first
    couple sweeps then throttles so a multi-hour outage can't flood the log."""
    attempt = 0
    while True:
        for port in ports:
            attempt += 1
            try:
                ib.connect(host, port, clientId=client_id, timeout=timeout)
                accts = ib.managedAccounts()
                if accts and all(a.startswith(account_prefix) for a in accts):
                    tag = "PRIMARY" if port == ports[0] else "FAILOVER"
                    log.info(f"FAILOVER-CONNECT: connected on {port} ({tag}); accounts {accts}")
                    return port
                log.error(f"FAILOVER-CONNECT: {port} account {accts} not '{account_prefix}*' "
                          f"— refusing (paper-only) + disconnecting")
                try:
                    ib.disconnect()
                except Exception:
                    pass
            except Exception as e:
                if attempt <= 2 * len(ports) or attempt % (5 * len(ports)) == 0:
                    log.warning(f"FAILOVER-CONNECT: {port} failed (attempt {attempt}): {e!r}")
                try:
                    ib.disconnect()
                except Exception:
                    pass
        time.sleep(sweep_sleep)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lg = logging.getLogger("failover-selftest")

    class _StubIB:
        def __init__(s, ok_port, acct="DUQ621725"):
            s.ok_port = ok_port; s.acct = acct
        def connect(s, h, port, clientId, timeout):
            if port != s.ok_port:
                raise ConnectionRefusedError(f"no Gateway on {port}")
        def managedAccounts(s):
            return [s.acct]
        def disconnect(s):
            pass

    assert other_port(4003) == 4002 and other_port(4002) == 4003 and other_port(9999) == 4003
    assert connect_failover(_StubIB(4003), "h", 30, "DU", lg) == 4003          # primary up
    assert connect_failover(_StubIB(4002), "h", 30, "DU", lg) == 4002          # primary down -> failover
    print("SELF-TEST OK: other_port + connect_failover (primary + failover paths)")

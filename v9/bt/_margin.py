# -*- coding: utf-8 -*-
"""Query IB for the REAL MNQ margin requirement (whatIf — places NO order).
Read-only. Uses a spare clientId so it cannot disturb any running bot."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, '/root/v9')
from ib_async import IB, Future, MarketOrder

ib = IB()
ib.connect('127.0.0.1', 7497, clientId=77, timeout=20)   # 77 = spare, not used by any bot
print("connected:", ib.managedAccounts())

from contract_roll import pick_front_contract
try:
    c = pick_front_contract(ib, 'MNQ', 'CME', 'USD')
    if isinstance(c, tuple): c = c[0]
except Exception:
    c = Future(symbol='MNQ', exchange='CME', currency='USD', lastTradeDateOrContractMonth='202609')
    ib.qualifyContracts(c)
print("contract:", c.localSymbol, c.lastTradeDateOrContractMonth)

# account values
av = {v.tag: v.value for v in ib.accountValues() if v.currency in ('USD', '')}
for k in ('NetLiquidation', 'FullInitMarginReq', 'FullMaintMarginReq', 'AvailableFunds', 'BuyingPower'):
    if k in av: print(f"  account {k}: {av[k]}")

# whatIf a 1-lot buy -> margin impact (NO order is transmitted)
for qty in (1, 2, 4):
    o = MarketOrder('BUY', qty)
    st = ib.whatIfOrder(c, o)
    init = getattr(st, 'initMarginChange', None) or getattr(st, 'initMarginAfter', None)
    maint = getattr(st, 'maintMarginChange', None) or getattr(st, 'maintMarginAfter', None)
    print(f"  qty {qty}: initMarginChange={init}  maintMarginChange={maint}")

# last price for context
try:
    t = ib.reqMktData(c, '', True, False); ib.sleep(2)
    px = t.last if t.last == t.last else (t.close if t.close == t.close else None)
    print("  MNQ last:", px, " notional/contract $", round((px or 0) * 2, 0))
except Exception as e:
    print("  price err", e)
ib.disconnect()
print("DONE (no order placed)")

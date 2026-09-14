# -*- coding: utf-8 -*-
"""MNQ margin scaling + live TIF check. Read-only (whatIf places NO order)."""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, '/root/v9')
from ib_async import IB, Future, MarketOrder

ib = IB(); ib.connect('127.0.0.1', 7497, clientId=79, timeout=20)
c = Future(conId=793356225, exchange='CME'); ib.qualifyContracts(c)

print("--- MNQ margin scaling (whatIf, no orders placed) ---")
for q in (1, 2, 4, 10):
    o = MarketOrder('BUY', q); o.tif = 'DAY'
    st = ib.whatIfOrder(c, o)
    ic = float(st.initMarginChange); mc = float(st.maintMarginChange); cm = float(st.commission)
    print("  qty %2d: initChange $%10s  maintChange $%9s  perContract init $%s maint $%s  comm $%.2f"
          % (q, format(ic, ',.0f'), format(mc, ',.0f'), format(ic/q, ',.0f'), format(mc/q, ',.0f'), cm))

av = {v.tag: v.value for v in ib.accountValues() if v.currency in ('USD', '')}
print("\n  NetLiquidation  $", av.get('NetLiquidation'))
print("  AvailableFunds  $", av.get('AvailableFunds'))
print("  FullInitMargin  $", av.get('FullInitMarginReq'))

# ---- TIF audit: are our live protective stops actually GTC, or did a preset rewrite them to DAY? ----
print("\n--- LIVE working orders: TIF audit (a TWS preset can rewrite GTC->DAY) ---")
ib.reqAllOpenOrders(); ib.sleep(2)
rows = [t for t in ib.openTrades()]
if not rows:
    print("  no working orders right now (book is flat) — cannot audit TIF live.")
for t in rows:
    o = t.order
    print("  %-8s %-6s %-4s qty %-3s tif=%-4s oca=%-22s status=%s"
          % (getattr(t.contract, 'localSymbol', '?'), o.orderType, o.action, o.totalQuantity,
             o.tif, (o.ocaGroup or '-')[:22], t.orderStatus.status))
ib.disconnect()
print("\nDONE (no order placed)")

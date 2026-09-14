import json, urllib.request
from datetime import datetime, timezone
key = open("/root/v9/.polygon_key").read().strip()

def q(tk, s, e, order="asc"):
    sn = int(datetime(*s, tzinfo=timezone.utc).timestamp() * 1e9)
    en = int(datetime(*e, tzinfo=timezone.utc).timestamp() * 1e9)
    u = (f"https://api.polygon.io/futures/v1/aggs/{tk}?resolution=1minute"
         f"&window_start.gte={sn}&window_start.lt={en}&order={order}&limit=10&apiKey={key}")
    try:
        with urllib.request.urlopen(u, timeout=30) as r:
            d = json.load(r)
    except Exception as ex:
        print(f"  {tk} {s}->{e}: ERROR {ex}")
        return
    res = d.get("results", [])
    print(f"  {tk} {s}->{e} order={order}: {len(res)} rows; status={d.get('status')}")
    for x in res[:4]:
        ts = x.get("window_start")
        print("    ", datetime.fromtimestamp(ts / 1e9, tz=timezone.utc), "close", x.get("close"))

print("== latest bars NQU6 (desc) whole window ==")
q("NQU6", (2026, 7, 30, 0, 0), (2026, 8, 4, 0, 0), "desc")
print("== Aug1-4 window asc ==")
q("NQU6", (2026, 8, 1, 0, 0), (2026, 8, 4, 0, 0), "asc")

u = f"https://api.polygon.io/futures/vX/contracts?product_code=NQ&limit=30&apiKey={key}"
try:
    with urllib.request.urlopen(u, timeout=30) as r:
        d = json.load(r)
    print("contracts:", [c.get("ticker") for c in d.get("results", [])][:20])
except Exception as e:
    print("contracts err", e)

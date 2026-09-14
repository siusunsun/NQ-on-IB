"""Can Polygon serve an EXPIRED contract's historical bars? Decides whether the roll
guard SPLICES (better) or DROPS pre-roll bars (safe fallback)."""
import sys, json, urllib.request, datetime as dt
sys.path.insert(0,'/root/v9'); sys.path.insert(0,'/root/v9/tools')
import v9_pull_latest_data as V
key = V.POLYGON_KEY_FILE.read_text().strip()
def pull(ticker, s, e):
    sn=int(dt.datetime(*s, tzinfo=dt.timezone.utc).timestamp()*1e9)
    en=int(dt.datetime(*e, tzinfo=dt.timezone.utc).timestamp()*1e9)
    url=(f"https://api.polygon.io/futures/v1/aggs/{ticker}?resolution=1minute"
         f"&window_start.gte={sn}&window_start.lt={en}&order=asc&limit=5000&apiKey={key}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            return json.load(r).get("results", []), None
    except Exception as ex:
        return None, repr(ex)
# NQM6 was front until the 2026-06-12 roll; pull a window while it was alive
rows, err = pull("NQM6", (2026,6,8), (2026,6,11))
print("NQM6 over 2026-06-08..11 (while it was FRONT):",
      ("ERROR "+err) if err else f"{len(rows)} bars")
if rows:
    cl=[r['close'] for r in rows]; print("   close %.2f .. %.2f" % (min(cl),max(cl)))
# same window under NQU6 (which was NOT yet front) - this is what the bug would write
rows2, err2 = pull("NQU6", (2026,6,8), (2026,6,11))
print("NQU6 over the SAME window (the contaminating source):",
      ("ERROR "+err2) if err2 else f"{len(rows2)} bars")
if rows2:
    cl=[r['close'] for r in rows2]; print("   close %.2f .. %.2f" % (min(cl),max(cl)))
if rows and rows2:
    m={int(r['window_start']):r['close'] for r in rows}
    d=[r2['close']-m[int(r2['window_start'])] for r2 in rows2 if int(r2['window_start']) in m]
    if d:
        import statistics
        print(f"\noverlap {len(d)} minutes | NQU6 - NQM6 = mean {statistics.mean(d):+.1f}pt "
              f"(min {min(d):+.1f} max {max(d):+.1f})")
        print("^ magnitude of the error the bug would have injected into pre-roll bars")

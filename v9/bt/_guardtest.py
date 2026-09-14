"""Force the roll-guard path: pretend the roll happened mid-window and confirm the
previous-contract pull + splice actually works. Writes NOTHING to the data dir."""
import sys, datetime as dt
sys.path.insert(0,'/root/v9'); sys.path.insert(0,'/root/v9/tools')
import v9_pull_latest_data as V

# 1. can Polygon actually serve the PREVIOUS (expired) contract?
prev = V._polygon_prev_ticker()
print("prev ticker:", prev)
prows, perr = V._polygon_pull(prev)
print("prev-contract pull:", ("ERROR: "+str(perr)) if perr else f"OK {len(prows)} bars")
if prows:
    import datetime
    ts=[int(r['window_start']) for r in prows]
    print("   range:", datetime.datetime.fromtimestamp(min(ts)/1e9, dt.timezone.utc),
          "->", datetime.datetime.fromtimestamp(max(ts)/1e9, dt.timezone.utc))
    cl=[r['close'] for r in prows]
    print("   close range: %.2f .. %.2f" % (min(cl), max(cl)))

# 2. front contract for comparison
front = V._polygon_front_ticker()
frows, ferr = V._polygon_pull(front)
print("front pull:", ("ERROR: "+str(ferr)) if ferr else f"OK {len(frows)} bars")
if frows:
    cl=[r['close'] for r in frows]
    print("   close range: %.2f .. %.2f" % (min(cl), max(cl)))

# 3. price gap between the two contracts over the same minutes = what the bug would inject
if prows and frows:
    pmap={int(r['window_start']):r['close'] for r in prows}
    diffs=[frows_c['close']-pmap[int(frows_c['window_start'])] for frows_c in frows
           if int(frows_c['window_start']) in pmap]
    if diffs:
        import statistics
        print(f"\noverlapping minutes: {len(diffs)}")
        print("   front - prev price diff: mean %.1f  min %.1f  max %.1f"
              % (statistics.mean(diffs), min(diffs), max(diffs)))
        print("   ^ this is the error the bug would have written into pre-roll bars")
    else:
        print("\nno overlapping minutes between contracts (prev already expired/illiquid)")

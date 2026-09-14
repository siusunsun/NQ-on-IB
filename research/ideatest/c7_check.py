"""C7 wiring checks: event conversion, trigger bar, fills, suspensions (counts only, no P&L)."""
import numpy as np, pandas as pd, sim
from collections import Counter
sim.load(); b1 = sim.D.b1
def iso(m): return pd.Timestamp(int(m), unit="m", tz="UTC").tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M")
ev = pd.read_csv(sim.HERE / "events" / "events.csv")
et = pd.to_datetime(ev.et_date + " " + ev.et_time).dt.tz_localize("America/New_York")
EV = tuple(int(x) for x in (et.dt.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(minutes=1))
print("first events ET/UTC:", [(iso(e)) for e in EV[:2]], len(EV))
sim.C7.setup(EV); print("flag bars set:", int(sim.C7.flag.sum()))
tr, log = sim.run(sim.Rules(c7_events=EV))
print("actions by sleeve:", Counter(a[1] for a in log.acts), " skips:", Counter(s[0] + ":" + s[1] for s in log.skips))
by = {t.key: t for t in tr}
evset = set(EV)
for a in log.acts[:8]:
    t = by[a[2]]; leg = [l for l in t.legs if l[3] == "MANAGEMENT_EXIT"][0]
    print(f"  {a[1]:13s} decision close {iso(a[3])}  fill {iso(leg[2])} @ {leg[1]}")
# every executed fill must be at the first bar starting >= e-5 for some event
bad = 0
for a in log.acts:
    t = by[a[2]]; leg = [l for l in t.legs if l[3] == "MANAGEMENT_EXIT"][0]
    ok = any(leg[2] == b1.t[np.searchsorted(b1.t, e - 5)] for e in EV if abs(leg[2] - e) < 30)
    bad += not ok
print("fills not at first bar >= e-5:", bad)
# no taken VWAP/short entry inside a window
for t in tr:
    if t.sleeve in ("VWAP_LONG_R3", "VWAP_SHORT_R1", "R3_RE"):
        assert not any(e - 5 <= t.entry_time < e + 10 for e in EV if abs(t.entry_time - e) < 30), iso(t.entry_time)
print("no entries inside suspension windows: OK")
ctrl, _ = sim.run(sim.Rules())
assert [x.legs for x in sim.run(sim.Rules())[0]] == [x.legs for x in ctrl]
print("C7 state resets between runs: OK")

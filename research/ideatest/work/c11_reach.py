"""Upper bound for idea #11 without ES: every trade where the NQ-side conditions
(RTH, >=3 post-entry bars, u<=-0.25 and MFE<0.25R on 2 consecutive checks) hold.
An exit there is the most #11 could ever do; the ES Z-filter can only remove activations."""
import numpy as np, pandas as pd, sim
sim.load(); b1 = sim.D.b1
ctrl, _ = sim.run(sim.Rules())
sig = {(n, i): s for i, n, s, _ in sim.D.signals}
re_by_parent = {t.parent_key: t for t in ctrl if t.sleeve == "R3_RE"}
rows = []
for tr in ctrl:
    if tr.sleeve not in ("VWAP_LONG_R3", "TLB_HYB40"):
        continue
    s = sig[tr.key]; E, R = s.entry, s.risk
    a = int(np.searchsorted(b1.t, tr.entry_time)); z = int(np.searchsorted(b1.t, tr.exit_time))
    if tr.sleeve == "TLB_HYB40":
        z = int(np.searchsorted(b1.t, tr.exit_time - 5))   # TLB exit stamped at bucket end
    mfe, nb, streak, date0 = 0.0, 0, 0, None
    for k in range(a, z):
        mfe = max(mfe, b1.h[k] - E)
        if not (k + 1 == len(b1.t) or b1.t[k + 1] // 5 != b1.t[k] // 5):
            continue
        nb += 1
        em = int(b1.minute[k] // 5 * 5 + 5)
        d = b1.dates[k]
        if d != date0: streak, date0 = 0, d
        ok = nb >= 3 and 585 <= em <= 960 and (b1.c[k] - E) / R <= -0.25 and mfe < 0.25 * R
        streak = streak + 1 if ok else 0
        if streak >= 2 and k + 1 < len(b1.t):
            exit_px = b1.o[k + 1]
            ctrl_net = tr.pts * 2
            new_net = tr.qty * (exit_px - E) * 2
            lost_re = re_by_parent.get(tr.key)
            lost = lost_re.pts * 2 - 5.4 if lost_re is not None else 0.0
            rows.append(dict(sleeve=tr.sleeve, date=d, ctrl=ctrl_net, early=new_net,
                             diff=new_net - ctrl_net - lost))
            break
df = pd.DataFrame(rows)
print("trades eligible (upper bound on #11 activations), 2021-08..2026-08:")
for sl, g in df.groupby("sleeve"):
    print(f"  {sl:13s} n={len(g):3d}  exits better {int((g['diff']>0).sum())}, worse {int((g['diff']<0).sum())}  "
          f"sum if ALL exited {g['diff'].sum():+8.0f}   ORACLE (only the good ones) {g['diff'].clip(lower=0).sum():+8.0f}")
print(f"  total ORACLE ceiling ${df['diff'].clip(lower=0).sum():,.0f} over 5 yrs; exit-all {df['diff'].sum():+,.0f}")

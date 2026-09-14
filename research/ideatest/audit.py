"""Independent audit of every executed management action (written separately from sim.py's loops).

For each rule it recomputes, from the raw 1-min / 5-min arrays with vectorised numpy, that
(a) the trigger condition held at the logged decision time, (b) it did NOT hold at any earlier
decision point of that trade (first trigger), (c) no original stop/target/flatten fired before the
fill, and (d) the fill is the open of the first 1-min bar at/after the decision time.
Prints counts only — no P&L.
"""
import math
from collections import Counter

import numpy as np

import sim

sim.load()
D = sim.D
b1, b5 = D.b1, D.b5
SIG = {(name, i): (sig, piv) for i, name, sig, piv in D.signals}


def minute_slice(t0, t1):
    a = int(np.searchsorted(b1.t, t0, side="left"))
    z = int(np.searchsorted(b1.t, t1, side="left"))
    return a, z


def bucket_ends(a, z):
    """Decision times (bucket ends) for completed buckets among 1-min rows a..z-1."""
    bk = b1.t[a:z] // 5 * 5
    last = np.r_[bk[1:] != bk[:-1], True]
    return (bk[last] + 5).astype(np.int64), np.arange(a, z)[last]


def check_vwap(rule, tr, act):
    name, i = tr.key
    sig, _ = SIG[(name, i)]
    long = sig.direction == "LONG"
    s = 1 if long else -1
    E, R = sig.entry, sig.risk
    dec = act[3]
    a, z = minute_slice(tr.entry_time, dec)
    ends, lastrow = bucket_ends(a, z)
    assert ends[-1] == dec, "decision not at a bucket end"
    # no original exit before decision
    lo, hi = b1.l[a:z], b1.h[a:z]
    assert not ((lo <= sig.stop).any() if long else (hi >= sig.stop).any()), "stop before decision"
    assert not ((hi >= sig.target).any() if long else (lo <= sig.target).any()), "target before decision"
    assert not ((b1.minute[a:z] >= 955) & (b1.minute[a:z] < 1020)).any(), "flatten before decision"
    fav = (hi - E) if long else (E - lo)
    mfe_run = np.maximum.accumulate(np.maximum(fav, 0))
    conds = []
    for nb, (e, r) in enumerate(zip(ends, lastrow), start=1):
        C = b1.c[r]
        u = s * (C - E) / R
        mfe = mfe_run[r - a]
        if rule == "c1":
            ok = nb >= 15 and mfe < 0.25 * R and u <= -0.5
        elif rule == "c2":
            jb = D.lookup5.get(int(e - 5))
            ok = False
            if nb >= 6 and jb is not None and jb >= 6 and b5.t[jb] // 1440 == b5.t[jb - 6] // 1440:
                vt, v6 = D.vwap5[jb], D.vwap5[jb - 6]
                ok = s * (vt - v6) <= -0.25 * R and s * (C - vt) < 0 and u <= -0.25
        elif rule == "c8":
            em = int(b1.minute[r] // 5 * 5 + 5)
            ok = False
            if 925 <= em < 955:
                x = np.diff(b1.c[r - 60:r + 1])
                sg = math.sqrt(np.mean(x * x))
                ok = s * (C - E) < 0 and sg > 0 and -s * (C - E) > 2 * sg * math.sqrt(955 - em)
        conds.append(ok)
    assert conds[-1], "condition false at decision"
    assert not any(conds[:-1]), "fired earlier than logged"
    return fill_check(tr, dec, "MANAGEMENT_EXIT")


def check_c6(tr, act):
    name, i = tr.key
    sig, _ = SIG[(name, i)]
    E = sig.entry
    dec = act[3]                       # 1-min bar start time of the decision bar
    a = int(np.searchsorted(b1.t, tr.entry_time, side="left"))
    k = int(np.searchsorted(b1.t, dec, side="left"))
    assert not (b1.l[a:k + 1] <= sig.stop).any() and not (b1.h[a:k + 1] >= sig.target).any()
    ok_list = []
    for kk in range(a, k + 1):
        if kk < 70 or (np.diff(b1.t[kk - 70:kk + 1]) > 30).any():
            ok_list.append(False)
            continue
        x = np.diff(b1.c[kk - 70:kk + 1])
        neg = np.minimum(x, 0) ** 2
        qr, qp = neg[-10:].mean(), neg[:60].mean()
        ok_list.append(qp > 0 and qr >= 4 * qp and b1.c[kk] < E)
    assert ok_list[-1] and not any(ok_list[:-1]), "c6 trigger timing"
    leg = [lg for lg in tr.legs if lg[3] == "MGMT_CUT"][0]
    assert leg[0] == 1 and leg[2] == b1.t[k + 1] and leg[1] == b1.o[k + 1], "c6 fill"
    return True


def check_c3(tr, act):
    name, i = tr.key
    sig, piv = SIG[(name, i)]
    (i1, v1), (i2, v2) = piv
    slope = (v2 - v1) / max(1, i2 - i1)
    E, R = sig.entry, sig.risk
    dec = act[3]
    j = D.lookup5[int(dec - 5)]
    ks = np.arange(i + 1, j + 1)
    assert not (b5.l[ks] <= sig.stop).any() and not (b5.h[ks] >= sig.target).any() and not (b5.minute[ks] >= 920).any()
    below = b5.c[ks] < v2 + slope * (ks - i2) - 0.25 * R
    mfe = np.maximum.accumulate(np.maximum(b5.h[ks] - E, 0))
    conds = [n >= 2 and below[n - 1] and below[n - 2] and mfe[n - 1] < 0.5 * R for n in range(1, len(ks) + 1)]
    assert conds[-1] and not any(conds[:-1]), "c3 trigger timing"
    return fill_check(tr, dec, "MANAGEMENT_EXIT")


def check_c4(tr, act):
    E, S0 = tr.entry, tr.stop
    dec = act[3]
    a, z = minute_slice(tr.entry_time, dec)
    ends, lastrow = bucket_ends(a, z)
    # which target type: recover from sim's rule (VWAP if fill < lag vwap of breakout bar)
    j = tr.key[1]
    kind = "VWAP" if E < D.vwap5[j - 1] else "UPPER"
    lv = D.vwap5 if kind == "VWAP" else D.upper5
    streak, fired = 0, []
    for e, r in zip(ends, lastrow):
        C = b1.c[r]
        jb = D.lookup5.get(int(e - 5))
        tt = math.nan if jb is None else lv[jb]
        if math.isnan(tt):
            streak = 0
        else:
            cond = (C - E) <= 0 and max(tt - C, 0) <= 0.5 * max(C - S0, 0) + 2.7
            streak = streak + 1 if cond else 0
        fired.append(streak >= 2)
    assert fired[-1] and not any(fired[:-1]), "c4 trigger timing"
    assert not (b1.l[a:z] <= S0).any()
    return fill_check(tr, dec, "MANAGEMENT_EXIT")


def fill_check(tr, dec, reason):
    k = int(np.searchsorted(b1.t, dec, side="left"))
    leg = tr.legs[-1]
    assert leg[3] == reason, f"last leg {leg[3]}"
    assert leg[2] == b1.t[k] and leg[1] == b1.o[k], "fill is not next 1-min open"
    return True


def main():
    ctrl, _ = sim.run(sim.Rules())
    ctrl_keys = {tr.key: tr for tr in ctrl}
    for rule in ("c1", "c2", "c3", "c4", "c6", "c8"):
        trades, log = sim.run(sim.Rules(**{rule: True}))
        by = {tr.key: tr for tr in trades}
        n, bad = 0, Counter()
        for act in log.acts:
            tr = by[act[2]]
            try:
                if rule == "c3":
                    check_c3(tr, act)
                elif rule == "c4":
                    check_c4(tr, act)
                elif rule == "c6":
                    check_c6(tr, act)
                else:
                    check_vwap(rule, tr, act)
                n += 1
            except AssertionError as e:
                bad[str(e)] += 1
        # every trade that the rule did NOT touch must equal its control twin, unless the path changed
        mgmt = {a[2] for a in log.acts}
        untouched_diff = sum(1 for k, tr in by.items() if k not in mgmt and k in ctrl_keys
                             and tr.legs != ctrl_keys[k].legs)
        print(f"{rule}: {len(log.acts)} actions, {n} verified, failures {dict(bad)}, "
              f"untouched-but-different trades {untouched_diff}")
    # C5 / C9 / B1 structural checks
    trades, log = sim.run(sim.Rules(c9=True))
    for _, sl, key in log.skips:
        sig, _ = SIG[key]
        assert sig.risk < 13.5 or sig.risk <= 0, "c9 skipped a wide signal"
    print(f"c9: {len(log.skips)} skips, all Rplan < 13.5pt")
    trades, log = sim.run(sim.Rules(c5=True))
    adm = Counter((a[0], a[2], a[3]) for a in log.admit)
    print("c5 admissions (sleeve, requested, admitted):", dict(adm))
    trades, _ = sim.run(sim.Rules(r3_qty=1))
    print("B1 R3 qty set:", Counter(tr.qty for tr in trades if tr.sleeve == "VWAP_LONG_R3"))
    t2, _ = sim.run(sim.Rules(record=True))
    assert [tr.legs for tr in t2] == [tr.legs for tr in ctrl], "record mode changed the book"
    print("record mode leaves the book unchanged: OK")


if __name__ == "__main__":
    main()

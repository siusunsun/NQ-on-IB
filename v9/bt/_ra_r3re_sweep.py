"""_ra_r3re_sweep.py - R3_RE parameter sensitivity on FULL history + era split.
Parent = deployed VWAP_LONG_R3 trades from _hist_tr_VWAP_LONG_R3.csv (STOP exits only).
Mirrors the _hist_sleeves.py overlay exactly at the deployed setting. READ-ONLY.
"""
import sys, importlib.util
import numpy as np, pandas as pd
_s = importlib.util.spec_from_file_location("_ra_common", "/root/v9/bt/_ra_common.py")
C = importlib.util.module_from_spec(_s); sys.modules["_ra_common"] = C; _s.loader.exec_module(C)
bt = C.bt; vsl = C.vsl; ET = C.ET
usv = vsl.update_session_vwap; sess_key = vsl.session_for_utc_bar

E = C.load_env()
df = E["df"]; bars5 = E["bars5"]
print("[load] 5min=%d" % len(bars5), flush=True)

n = len(bars5); btsA = [b[0] for b in bars5]
o5 = np.array([b[1] for b in bars5]); h5 = np.array([b[2] for b in bars5])
l5 = np.array([b[3] for b in bars5]); c5 = np.array([b[4] for b in bars5]); v5 = np.array([b[5] for b in bars5])
K = 2.0
vwap5 = np.full(n, np.nan); sd5 = np.full(n, np.nan); skA = []
cur = None; cumv = cumvp = cumsq = 0.0
for i in range(n):
    sk = sess_key(btsA[i].to_pydatetime(), 0); skA.append(sk)
    if sk != cur:
        cur = sk; cumv = cumvp = cumsq = 0.0
    cumv, cumvp, cumsq, vw, sd = usv(cumv, cumvp, cumsq, h5[i], l5[i], c5[i], v5[i])
    vwap5[i] = vw; sd5[i] = sd
upper5 = vwap5 + K * sd5
bucket_idx = {btsA[i]: i for i in range(n)}

def floor5(ts):
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("5min")

def et_min(ts):
    e = ts.tz_convert(ET); return e.hour * 60 + e.minute

FLAT = 15 * 60 + 55
idx1 = df.index; h1 = df["high"].values; l1 = df["low"].values; c1 = df["close"].values

par = pd.read_csv("/root/v9/bt/_hist_tr_VWAP_LONG_R3.csv")
par["entry_utc"] = pd.to_datetime(par["entry_utc"], utc=True)
par["exit_utc"] = pd.to_datetime(par["exit_utc"], utc=True)
par = par[par.exit_reason == "STOP"].reset_index(drop=True)
print("  parent STOP-outs = %d" % len(par), flush=True)


def run(wait=2, stop_src="si", magnet="adaptive", max_wait=None):
    fires = []
    for _, tr in par.iterrows():
        si = bucket_idx.get(floor5(tr["entry_utc"] - pd.Timedelta(minutes=5)))
        ki = bucket_idx.get(floor5(tr["exit_utc"]))
        if si is None or ki is None:
            continue
        sess = skA[ki]; entry = None; j = ki + wait
        scanned = 0
        while j < n and skA[j] == sess and et_min(btsA[j]) < FLAT:
            if max_wait is not None and scanned >= max_wait:
                break
            runmax = h5[ki:j].max() if j > ki else h5[ki]
            if h5[j] > runmax:
                fill = max(o5[j], runmax)
                if stop_src == "si":
                    stop_lvl = l5[si:j].min()
                elif stop_src == "ki":
                    stop_lvl = l5[ki:j].min() if j > ki else l5[ki]
                else:   # fixed 20-bar lookback before j
                    stop_lvl = l5[max(0, j - 20):j].min()
                lag_vw = vwap5[j - 1]
                if np.isnan(lag_vw):
                    j += 1; scanned += 1; continue
                if magnet == "adaptive":
                    kind = "VWAP" if fill < lag_vw else "UPPER"
                elif magnet == "vwap":
                    kind = "VWAP"
                elif magnet == "upper":
                    kind = "UPPER"
                else:  # inverted
                    kind = "UPPER" if fill < lag_vw else "VWAP"
                riskpt = fill - stop_lvl
                if riskpt <= 0:
                    entry = "BAD"; break
                entry = dict(j=j, fill=fill, stop=stop_lvl, kind=kind, riskpt=riskpt, sess=sess); break
            j += 1; scanned += 1
        if entry in (None, "BAD"):
            continue
        j = entry["j"]; fill = entry["fill"]; stop_lvl = entry["stop"]; kind = entry["kind"]
        start = btsA[j] + pd.Timedelta(minutes=5)
        m0 = idx1.searchsorted(start, side="left"); m = m0
        ex_px = ex_reason = ex_ts = None
        while m < len(idx1):
            t = idx1[m]
            if sess_key(t.to_pydatetime(), 0) != entry["sess"]:
                ex_px = c1[m - 1] if m > m0 else fill; ex_reason = "SESS_END"; ex_ts = idx1[m - 1]; break
            if et_min(t) >= FLAT:
                ex_px = c1[m]; ex_reason = "FLATTEN"; ex_ts = t; break
            if l1[m] <= stop_lvl:
                ex_px = stop_lvl; ex_reason = "STOP"; ex_ts = t; break
            bi = bucket_idx.get(floor5(t) - pd.Timedelta(minutes=5))
            lvl = (vwap5[bi] if kind == "VWAP" else upper5[bi]) if bi is not None else np.nan
            if not np.isnan(lvl) and lvl > fill and h1[m] >= lvl:
                ex_px = lvl; ex_reason = "TARGET"; ex_ts = t; break
            m += 1
        if ex_px is None:
            ex_px = c1[-1]; ex_reason = "DATA_END"; ex_ts = idx1[-1]
        pts = ex_px - fill
        eu = btsA[j] + pd.Timedelta(minutes=5)
        fires.append((eu.tz_convert(ET).year, pts * C.MNQ, eu.tz_convert(ET).date()))
    return fires

def summarize(trs, tag):
    if not trs:
        return dict(tag=tag, n=0, sr=0.0)
    pnl = np.array([t[1] for t in trs]); yr = np.array([t[0] for t in trs])
    f = C.stat_block(pnl); e1 = C.stat_block(pnl[yr <= 2023]); e2 = C.stat_block(pnl[yr >= 2024])
    return dict(tag=tag, n=f["n"], net=round(f["net"]), pf=round(f["pf"], 3), sr=round(f["sr"], 4),
                n1=e1["n"], net1=round(e1["net"]), pf1=round(e1["pf"], 3),
                n2=e2["n"], net2=round(e2["net"]), pf2=round(e2["pf"], 3))

def show(d):
    print("  %-22s n=%-4d net=%-8s pf=%-6s | e1 n=%-4d net=%-8s pf=%-6s | e2 n=%-4d net=%-8s pf=%s"
          % (d["tag"], d["n"], d["net"], d["pf"], d["n1"], d["net1"], d["pf1"],
             d["n2"], d["net2"], d["pf2"]), flush=True)

rows = []
BASE = dict(wait=2, stop_src="si", magnet="adaptive", max_wait=None)
VARS = [("wait", [1, 2, 3]), ("stop_src", ["si", "ki", "fix20"]),
        ("magnet", ["adaptive", "vwap", "upper", "inverted"]),
        ("max_wait", [12, 24, 48, None])]
print("\n[one-at-a-time around wait=2 stop=si magnet=adaptive max_wait=None]", flush=True)
for p, vals in VARS:
    for v in vals:
        c = dict(BASE); c[p] = v
        d = summarize(run(**c), "R3RE|%s=%s" % (p, v)); d.update(param=p, value=str(v))
        rows.append(d); show(d)
pd.DataFrame(rows).to_csv("/root/v9/bt/_ra_r3re_sweep.csv", index=False)

print("\n[per-year, deployed cfg]", flush=True)
trs = run(**BASE)
pnl = np.array([t[1] for t in trs]); yr = np.array([t[0] for t in trs])
for y in sorted(set(yr)):
    b = C.stat_block(pnl[yr == y])
    print("   %d n=%-4d net=$%-8s pf=%.2f avg=$%.1f" % (y, b["n"], format(b["net"], ",.0f"), b["pf"], b["avg"]))
print("DONE", flush=True)

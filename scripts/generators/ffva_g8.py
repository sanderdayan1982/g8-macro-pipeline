#!/usr/bin/env python3
"""
G8 PORT — FFVA generator  [ffva_g8 v0.1.0]
==========================================
Ported from: FFVA-G8 — Futures Flow Velocity & Acceleration [v1.3.2] (13-jul-2026)
Question:    Does observable physical demand in regulated FX futures (CME/ICE)
             VALIDATE or CONTRADICT the rates signal read by the IYDT?

SCOPE OF THIS VERSION (core engine only — Sections 2 and 4 of the Pine):
  · roll detection (|ΔOI/OI[1]| ≥ 0.40, cooldown 3), ΔOI normalised by SMA-21 of OI,
    Z-252 capped ±3, velocity (Z − Z[5], EMA-3), acceleration (vel − vel[3]),
    Price×ΔOI quadrant, flow/dir, D1D/D1W/D1M trilogy, persistence, PMAX-252,
    exhaustion ratio, volume ratio vs SMA-21, status FRESH/STALE/ROLL.
  · DEFERRED (need other ported scripts): Layer B (IYDT cross, XDIV thresholds),
    Layer C (POL/PSI), XCCY & POL mirrors. They are funnel-level confluence and
    will be wired in Fase 5 from the other scripts' state files — not re-implemented here.

FRONT-CONTRACT RULE (twin-test relevant): the Pine reads TradingView continuous
`X1!` / `X1!_OI`. Here the front series is rebuilt from contract-level data as the
nearest-expiry QUARTERLY contract (H/M/U/Z) with expiry > session — CME FX serial
months are ignored, as TradingView does (1! rolls at expiration).
The OI-jump roll detector then behaves exactly as in Pine.

Inputs   data/futures/canonical/{ROOT}.csv   (fx_futures_collector.py)
Outputs  data/state/ffva_g8.json, data/twin/ffva_g8_py_history.csv
Frozen inputs (Rule 32) copied verbatim from the Pine `input.*` block: THRESHOLDS below.
Python 3.9+ · pandas/numpy.
"""
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FUT_DIR = ROOT / "data" / "futures" / "canonical"
REGISTRY = ROOT / "sources" / "registry.csv"
STATE_OUT = ROOT / "data" / "state" / "ffva_g8.json"
TWIN_HIST = ROOT / "data" / "twin" / "ffva_g8_py_history.csv"

SCRIPT, PINE_VERSION, PY_VERSION = "FFVA", "v1_3_2_EN", "0.1.1"
QUESTION = "Does physical demand in regulated FX futures (CME/ICE) validate or contradict the IYDT rates signal?"
CUTOFF_DATE = "2026-06-29"      # calibrate_ffva.py --multi, 29-jun-2026 (Gate M 3.1)
FORWARD_WINDOWS_BD = [5, 10, 20]

# ── frozen Pine inputs (v1.3.2, Section 1) ──
THRESHOLDS = {
    "roll_drop": 0.40, "roll_cooldown": 3, "stale_bars": 3,
    "oi_mean_len": 21, "z_len_hi": 252, "z_len_med": 252, "z_len_lo": 252, "z_cap": 3.0,
    "vel_lb": 5, "acc_lb": 3, "vel_smooth": 3,
    "z_flow": 0.25, "vol_hi": 1.5, "vol_lo": 0.5, "vol_len": 21, "pmax_len": 252, "exh_ratio": 0.8,
}
CCY = {"6E": ("EUR", "z_len_hi"), "6B": ("GBP", "z_len_hi"), "6J": ("JPY", "z_len_hi"),
       "6A": ("AUD", "z_len_med"), "6N": ("NZD", "z_len_lo"), "6C": ("CAD", "z_len_med"),
       "6S": ("CHF", "z_len_lo"), "DX": ("USD", "z_len_hi")}
FEED_BY_ROOT = {r: ("CME_%s" % r if r != "DX" else "ICE_DX") for r in CCY}


# ─────────────────────────────────────────────────────────────────────────────
def load_front(root):
    """Front-contract daily series: settle, oi, volume (nearest expiry > session)."""
    p = FUT_DIR / ("%s.csv" % root)
    if not p.exists():
        return None
    df = pd.read_csv(p, dtype=str)
    if df.empty:
        return None
    for c in ("settle", "oi", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[df["expiry"].notna() & (df["expiry"] != "")]
    df = df[df["expiry"] > df["session"]]
    # QUARTERLY ONLY (H/M/U/Z): CME FX also lists monthly "serial" contracts with tiny OI.
    # TradingView's X1! is the quarterly front; matching it is what the twin-test measures.
    df = df[df["expiry"].str[5:7].isin(["03", "06", "09", "12"])]
    front = df.sort_values(["session", "expiry"]).groupby("session").first().reset_index()
    front["session"] = pd.to_datetime(front["session"])
    return front.set_index("session")[["symbol", "expiry", "settle", "oi", "volume"]].sort_index()


# NA POLICY (declared, twin-test arbitrates): rolling windows count VALID prints and
# skip ROLL/NA bars ("NA = pause, not reset"). With quarterly rolls (~63 bars) a strict
# 252-bar window that voids on any NA would never fill; the Pine is calibrated on 21y
# of data and runs live, so the pause semantics is the only consistent reading.
NA_POLICY = "skip"


def _roll_valid(s, n, fn):
    v = s.dropna()
    r = fn(v.rolling(n, min_periods=n))
    return r.reindex(s.index)


def sma_full(s, n):
    if NA_POLICY == "skip":
        return _roll_valid(s, n, lambda w: w.mean())
    return s.rolling(n, min_periods=n).mean()


def std_full(s, n):
    if NA_POLICY == "skip":
        return _roll_valid(s, n, lambda w: w.std(ddof=0))
    return s.rolling(n, min_periods=n).std(ddof=0)   # Pine ta.stdev is population


def ema_pine(s, n):
    """ta.ema semantics: na input → na output; re-seeds with SMA(n) after a gap."""
    a = 2.0 / (n + 1)
    out, st = [], np.nan
    vals = s.tolist()
    for i, v in enumerate(vals):
        if pd.isna(v):
            st = np.nan
            out.append(np.nan)
            continue
        if pd.isna(st):
            w = vals[max(0, i - n + 1): i + 1]
            st = float(np.mean(w)) if len(w) == n and not any(pd.isna(x) for x in w) else np.nan
        else:
            st = a * v + (1 - a) * st
        out.append(st)
    return pd.Series(out, index=s.index)


def oi_side(oi, zlen):
    """Pine f_oi_side on the continuous OI series."""
    T = THRESHOLDS
    oi = oi.astype(float)
    prev = oi.shift(1)
    jumped = (oi.notna() & prev.notna() & (prev > 0) & ((oi - prev).abs() / prev >= T["roll_drop"]))
    is_roll = pd.Series(False, index=oi.index)
    cd = 0
    for i, j in enumerate(jumped.tolist()):
        if j:
            cd = T["roll_cooldown"]
        is_roll.iloc[i] = cd > 0
        if cd > 0:
            cd -= 1
    oi_c = oi.where(~is_roll)
    d_oi = oi_c - oi_c.shift(1)
    oi_m = sma_full(oi_c, T["oi_mean_len"])
    doi = (d_oi / oi_m).where(d_oi.notna() & oi_m.notna() & (oi_m > 0))
    zm, zs = sma_full(doi, zlen), std_full(doi, zlen)
    z0 = ((doi - zm) / zs).where(doi.notna() & zm.notna() & zs.notna() & (zs > 0))
    z = z0.clip(-T["z_cap"], T["z_cap"])
    dv = z - z.shift(T["vel_lb"])
    edv = ema_pine(dv, T["vel_smooth"])
    vel = edv.where(z.notna() & z.shift(T["vel_lb"]).notna())
    acc = (vel - vel.shift(T["acc_lb"])).where(vel.notna() & vel.shift(T["acc_lb"]).notna())
    return z, vel, acc, is_roll


def px_side(settle, volume):
    T = THRESHOLDS
    prev = settle.shift(1)
    pd_ = pd.Series(np.where(settle.isna() | prev.isna(), np.nan,
                             np.where(settle > prev, 1.0, np.where(settle < prev, -1.0, 0.0))), index=settle.index)
    vm = sma_full(volume, T["vol_len"])
    vr = (volume / vm).where(volume.notna() & vm.notna() & (vm > 0))
    return pd_, vr


def engine(z, is_roll, oi_raw, pd_, sessions, asof):
    """Pine f_engine on the contract's own bars; returns the LAST bar's tuple plus histories."""
    T = THRESHOLDS
    fl, last_ff, per, last_q, ph, pmax = [], np.nan, 0, None, [], 0.0
    hist = []
    for i in range(len(z)):
        zi, roll, p = z.iloc[i], bool(is_roll.iloc[i]), pd_.iloc[i]
        if roll:
            q = "ROLL"
        elif pd.isna(p) or pd.isna(zi):
            q = "NA"
        elif p > 0 and zi > T["z_flow"]:
            q = "ACCUM"
        elif p > 0 and zi < -T["z_flow"]:
            q = "SC"
        elif p < 0 and zi > T["z_flow"]:
            q = "DIST"
        elif p < 0 and zi < -T["z_flow"]:
            q = "LIQ"
        else:
            q = "FLAT_OI"
        az = 0.0 if pd.isna(zi) else abs(zi)
        flow = (np.nan if q in ("ROLL", "NA") else az if q == "ACCUM" else -az if q == "DIST"
                else -0.5 * az if q == "SC" else 0.5 * az if q == "LIQ" else 0.0)
        dirr = (az if q == "ACCUM" else -az if q == "DIST" else -0.5 * az if q == "SC"
                else 0.5 * az if q == "LIQ" else np.nan)
        push = last_ff if pd.isna(flow) else flow
        if not pd.isna(push):
            last_ff = push
        fl.insert(0, push)
        if len(fl) > 30:
            fl.pop()
        if not (roll or q in ("ROLL", "NA")):
            per = per + 1 if (last_q is not None and last_q == q) else 1
            last_q = q
        ph.insert(0, float(per))
        pmax = max(pmax, float(per))
        if len(ph) > T["pmax_len"]:
            old = ph.pop()
            if old >= pmax and ph:
                pmax = max(ph)
        n = len(fl)
        p1 = fl[1] if n > 1 else np.nan
        p5 = fl[5] if n > 5 else np.nan
        p21 = fl[21] if n > 21 else np.nan
        base = np.nan if (roll or pd.isna(flow)) else flow
        d1d = np.nan if (pd.isna(base) or pd.isna(p1)) else base - p1
        d1w = np.nan if (pd.isna(base) or pd.isna(p5)) else base - p5
        d1m = np.nan if (pd.isna(base) or pd.isna(p21)) else base - p21
        hist.append((sessions[i], q))
    last_sess = sessions[-1]
    age_days = (pd.Timestamp(asof) - pd.Timestamp(last_sess)).days
    st = "ROLL" if roll else ("STALE" if pd.isna(oi_raw.iloc[-1]) or age_days > T["stale_bars"] + 2 else "FRESH")
    return dict(quad=q, flow=flow, dir=dirr, d1d=d1d, d1w=d1w, d1m=d1m, per=per, pmax=pmax, status=st), hist


def nn(x, nd=3):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def canonical_hash(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode().strip()[:12]
    except Exception:
        return "0000000"


def twin_meta():
    p = ROOT / "data" / "twin" / "ffva_g8_twin.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"status": "NOT_STARTED", "started": None, "weeks_elapsed": 0, "state_discrepancy_pct": None, "acta": None}


def registry():
    with open(REGISTRY, newline="") as f:
        return {r["feed_id"]: r for r in csv.DictReader(f)}


# ─────────────────────────────────────────────────────────────────────────────
def main():
    asof = date.today().isoformat()
    reg = registry()
    T = THRESHOLDS
    inputs, per_ccy, alerts, hist_rows = [], {}, [], []

    for root, (ccy, zkey) in CCY.items():
        feed = FEED_BY_ROOT[root]
        r = reg.get(feed, {"calendar": "CME", "max_staleness_bd": "3"})
        front = load_front(root)
        if front is None or len(front) < 2:
            per_ccy[ccy] = {"score": 0.0, "state": "NA", "percentile": None, "z": None,
                            "window_bars": T[zkey], "thresholds_frozen": T, "components": {},
                            "na_reason": "no futures data for %s" % root}
            inputs.append({"feed_id": feed, "ccy": ccy, "metric": "fut_settle_oi", "value": 0.0,
                           "data_date": asof, "age_bd": 0, "quality": "DEAD", "source_used": "PRIMARY",
                           "calendar": r["calendar"], "plausibility": "OK"})
            alerts.append("FFVA %s: no data (%s)" % (ccy, feed))
            continue

        z, vel, acc, is_roll = oi_side(front["oi"], T[zkey])
        pd_, vr = px_side(front["settle"], front["volume"])
        sessions = [d.strftime("%Y-%m-%d") for d in front.index]
        e, hist = engine(z, is_roll, front["oi"], pd_, sessions, asof)
        # twin-test rows: Pine exports z_* (not the quadrant) -> compare the z bucket
        zb = ["NA" if pd.isna(v) else "Z+" if v > T["z_flow"] else "Z-" if v < -T["z_flow"] else "Z0" for v in z.tolist()]
        hist_rows += [(s, ccy, b) for s, b in zip(sessions, zb)]

        last = front.index[-1]
        age_bd = int(np.busday_count(last.date(), date.fromisoformat(asof)))
        max_st = int(r["max_staleness_bd"])
        quality = "LIVE" if age_bd <= max_st else ("STALE" if age_bd <= 2 * max_st else "DEAD")
        inputs.append({"feed_id": feed, "ccy": ccy, "metric": "fut_settle_oi", "value": float(front["oi"].iloc[-1] or 0),
                       "data_date": last.strftime("%Y-%m-%d"), "age_bd": age_bd, "quality": quality,
                       "source_used": "PRIMARY", "calendar": r["calendar"], "plausibility": "OK"})
        zl = z.iloc[-1]
        vr_l = vr.iloc[-1]
        mass = None if pd.isna(vr_l) else ("WITH MASS" if vr_l >= T["vol_hi"] else "EMPTY" if vr_l <= T["vol_lo"] else "NORMAL")
        exh = None if e["pmax"] <= 0 else e["per"] / e["pmax"]
        zwin = z.dropna()
        pct = None if len(zwin) < T[zkey] else float((zwin.iloc[-T[zkey]:] < zl).mean() * 100) if not pd.isna(zl) else None
        per_ccy[ccy] = {
            "score": nn(e["flow"]) if nn(e["flow"]) is not None else 0.0,
            "state": e["quad"],
            "percentile": nn(pct, 1), "z": nn(zl, 2), "window_bars": T[zkey],
            "thresholds_frozen": T,
            "components": {
                "front": str(front["symbol"].iloc[-1]), "expiry": str(front["expiry"].iloc[-1]),
                "settle": nn(front["settle"].iloc[-1], 5), "oi": nn(front["oi"].iloc[-1], 0),
                "vel": nn(vel.iloc[-1]), "acc": nn(acc.iloc[-1]), "dir": nn(e["dir"]),
                "d1d": nn(e["d1d"]), "d1w": nn(e["d1w"]), "d1m": nn(e["d1m"]),
                "persist": int(e["per"]), "pmax": int(e["pmax"]), "exhaustion": nn(exh),
                "vol_ratio": nn(vr_l, 2), "mass": mass, "status": e["status"],
                "bars_loaded": int(len(front)), "z_warm": bool(len(zwin) >= 1),
            },
            "na_reason": (None if e["quad"] not in ("NA", "ROLL") else
                          "ROLL cooldown" if e["quad"] == "ROLL" else
                          "Z-252 warm-up: %d bars loaded, need ~%d" % (len(front), T[zkey] + T["oi_mean_len"] + 12)),
        }
        if e["status"] != "FRESH":
            alerts.append("FFVA %s status %s (last session %s)" % (ccy, e["status"], sessions[-1]))
        if exh is not None and exh > T["exh_ratio"] and e["quad"] in ("SC", "LIQ"):
            alerts.append("FFVA %s exhaustion %s persist/PMAX=%.2f" % (ccy, e["quad"], exh))

    counts = {}
    for v in per_ccy.values():
        counts[v["state"]] = counts.get(v["state"], 0) + 1
    label = " ".join("%s:%d" % (k, counts[k]) for k in ("ACCUM", "DIST", "SC", "LIQ", "FLAT_OI", "ROLL", "NA") if k in counts)
    outputs = {"regime": {"label": label, "since": asof, "changed_this_run": False}, "per_ccy": per_ccy, "pairs": {}}

    prev = json.loads(STATE_OUT.read_text()) if STATE_OUT.exists() else None
    if prev:
        pr = prev["outputs"]["regime"]
        outputs["regime"]["changed_this_run"] = pr["label"] != label
        outputs["regime"]["since"] = asof if outputs["regime"]["changed_this_run"] else pr["since"]
        diff = [k for k, v in per_ccy.items() if prev["outputs"]["per_ccy"].get(k, {}).get("state") != v["state"]]
    else:
        diff = ["initial"]

    q = [i["quality"] for i in inputs]
    w = {"LIVE": 1.0, "STALE": 0.5, "DEAD": 0.0}
    tt = twin_meta()
    state = {
        "meta": {"script": SCRIPT, "question": QUESTION, "pine_version": PINE_VERSION, "py_version": PY_VERSION,
                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "run_id": os.environ.get("GITHUB_RUN_ID", "local"), "commit": git_commit(),
                 "schema_version": "1.0", "twin_test": tt, "funnel_eligible": tt["status"] == "PASSED"},
        "inputs": inputs, "outputs": outputs,
        "dqm": {"health_score": round(100.0 * sum(w[x] for x in q) / len(q), 1), "n_inputs": len(q),
                "n_live": q.count("LIVE"), "n_proxy": 0, "n_manual": 0, "n_stale": q.count("STALE"),
                "n_dead": q.count("DEAD"), "n_manual_expired": 0, "alerts": alerts},
        "gate_m": {"is_oos": asof > CUTOFF_DATE, "cutoff_date": CUTOFF_DATE,
                   "signal_snapshot": {k: {"state": v["state"], "score": v["score"]} for k, v in per_ccy.items()},
                   "forward_windows_bd": FORWARD_WINDOWS_BD, "realised": None},
        "journal_link": {"state_hash": canonical_hash(outputs),
                         "prev_state_hash": prev["journal_link"]["state_hash"] if prev else None,
                         "diff_vs_prev": diff},
    }
    STATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    STATE_OUT.write_text(json.dumps(state, indent=2))

    # twin history: full per-bar quadrant history, idempotent
    TWIN_HIST.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if TWIN_HIST.exists():
        with open(TWIN_HIST, newline="") as f:
            existing = {(r["data_date"], r["ccy_or_pair"]) for r in csv.DictReader(f)}
    else:
        TWIN_HIST.write_text("data_date,ccy_or_pair,state\n")
    with open(TWIN_HIST, "a", newline="") as f:
        wr = csv.writer(f)
        for s, c, qd in hist_rows:
            if (s, c) not in existing:
                wr.writerow([s, c, qd])
    print("FFVA state written: %s | health %.0f | diff %s" % (label, state["dqm"]["health_score"], diff))
    return 0


if __name__ == "__main__":
    sys.exit(main())

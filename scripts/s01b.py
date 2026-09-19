#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s01b.py v1.1 — §01-b · 22-session long-end reading + "compatible with fiscal tension" (CTF) flag
G8 Macro Pipeline · 19-Sep-2026 · spec: docs/actas/ESPEC_S01B_v1.md (v1.1) · acta: ACTA_S01B.md
State: CONTEXT — never votes. Stdlib only (same policy as dashboard_alerts.py).

USAGE
  python3 scripts/s01b.py                 # evaluate today's session if its FX is published (else PENDING)
  python3 scripts/s01b.py --final         # end-of-day run: evaluate even if FX is missing (DESFASE)
  python3 scripts/s01b.py --replay 2026-09-18       # recompute that session from its snapshot and diff vs log
  python3 scripts/s01b.py --annex         # retrospective replay of the 9 validate episodes (label RETROSPECTIVO)
  python3 scripts/s01b.py --dry-run       # compute and print, write nothing
Nonzero exit on replay mismatch or unrecoverable errors.

ONE EVALUATION PER SESSION · EVENT ≠ DELIVERY · ATOMIC PUBLICATION
  Session key t = today's UTC date if it is a TARGET business day. The session is evaluated exactly once:
  if data/s01b/log/<t>.json exists, nothing is recomputed (later runs the same day only re-publish
  S01B.json from that log). Events (OFF→ON) are recorded in the log and in data/s01b/events.jsonl;
  dashboard_alerts.py owns delivery and its own "sent" ledger. Publication order: snapshot → log →
  state → S01B.json, each via write-to-temp + os.replace; S01B.json carries the run_id of the log it
  reflects, so a partial run is detectable and self-heals on the next run.

REPRODUCIBILITY (data/s01b/snap/<t>/)
  ACM_G8_<CCY>.csv.gz ×8 (acm_g8.py rewrites history daily), calendar.txt.gz (TARGET dates from
  usd_factor canonical), fx_window.json (the 22 log-returns per currency and f actually used), and the
  log holds state_before, thresholds, population size and SHA-256 of every input. --replay proves it.
"""
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import sys
import re
import fcntl
from datetime import date, datetime, timedelta, timezone

VERSION = "s01b v1.1"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DATA = os.path.join(ROOT, "data")
S01B = os.path.join(DATA, "s01b")
LOG_DIR, SNAP_DIR, STAGE = os.path.join(S01B, "log"), os.path.join(S01B, "snap"), os.path.join(S01B, ".staging")
STATE_PATH, EVENTS_PATH, OUT_JSON = os.path.join(S01B, "state.json"), os.path.join(S01B, "events.jsonl"), os.path.join(DATA, "S01B.json")

CCY8 = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
EMITTERS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD"]         # CHF: reading only (D3)
H = 22                      # sessions
P_ON, P_OFF, P_HI = 80.0, 60.0, 95.0                                 # D1: expanding percentiles, signed population
MIN_POP = 252               # D1
FX_THR = -0.005             # D2: log return over the common window
FX_FAIL_STREAK = 3          # ON→OFF when FX condition fails 3 OK sessions in a row
MAX_LAG_BD = 3              # as-of lag allowed at each window end
MIN_QUALITY_MONTHS = 240    # acm_g8 MIN_OBS_MONTHLY

NOM_FILES = {c: "RY_G8_%s.csv" % c for c in CCY8 if c != "CHF"}
NOM_FILES["CHF"] = "CHF_SPOT_10Y.csv"
Y2_FILES = {"USD": "US_BILL_2Y.csv", "EUR": "EUR_BILL_2Y.csv", "GBP": "GBP_BILL_2Y.csv", "JPY": "JPY_BILL_2Y.csv",
            "NZD": "NZD_BOND_2Y.csv", "CHF": "CHF_SPOT_2Y.csv"}          # AUD/CAD: no daily 2Y in the repo → "·"
BE_FILES = {c: "RY_G8_%s.csv" % c for c in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD"]}   # NZD/CHF constants → "·"


# ─────────────────────────────────────────────────────────────────────────────
# small utilities
# ─────────────────────────────────────────────────────────────────────────────
def log(msg):
    print("[s01b] " + msg, flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def pct(sorted_vals, q):
    """linear-interpolated percentile of an already-sorted list (same rule as dashboard_alerts.pct)."""
    n = len(sorted_vals)
    if n == 0:
        return None
    k = (n - 1) * q / 100.0
    f = math.floor(k)
    c = min(f + 1, n - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def parse_date(s):
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def read_series(path, col=None):
    """CSV → sorted list[(date, float)] · accepts DATE/Date, CLOSE/Value/<col>, '#' comments."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rows = [l for l in fh if l.strip() and not l.startswith("#")]
    for r in csv.DictReader(rows):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        d = parse_date(r.get("DATE") or r.get("Date") or "")
        v = r.get(col) if col and r.get(col) not in (None, "") else (r.get("CLOSE") or r.get("Value"))
        if d is None or v in (None, "", "NA", "nan", "NaN"):
            continue
        try:
            if math.isfinite(float(v)):
                out.append((d, float(v)))
        except ValueError:
            continue
    out.sort()
    return out


def read_acm(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for r in csv.DictReader(fh):
            d = parse_date(r.get("DATE"))
            try:
                values = [float(r[k]) for k in ("TP10", "RNY10", "Y10_FIT")]
                if d and all(math.isfinite(v) for v in values):
                    out.append((d, *values, (r.get("QUALITY") or "").strip()))
            except (KeyError, TypeError, ValueError):
                continue
    out.sort()
    return out


def read_canonical(path):
    """usd_factor canonical.csv → (dates, f[], {ccy: r[]}, desfase[]) on the TARGET calendar."""
    dates, f, r, des = [], [], {c: [] for c in CCY8 if c != "USD"}, []
    if not os.path.exists(path):
        return dates, f, r, des
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rd = csv.reader(fh)
        head = next(rd)
        idx = {h: i for i, h in enumerate(head)}
        for row in rd:
            d = parse_date(row[0])
            if d is None:
                continue
            try:
                fv = float(row[idx["f"]])
            except (KeyError, ValueError):
                continue
            if not math.isfinite(fv):
                continue
            dates.append(d)
            f.append(fv)
            for c in r:
                try:
                    value = float(row[idx["r_" + c]])
                    r[c].append(value if math.isfinite(value) else None)
                except (KeyError, ValueError):
                    r[c].append(None)
            des.append((row[idx["desfase_us"]] if "desfase_us" in idx else "").strip() == "True")
    return dates, f, r, des


def bdays(d0, d1):
    """business days (Mon-Fri) strictly between d0 and d1 (d0 < d1) → int."""
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def asof(series, d, max_lag_bd=MAX_LAG_BD):
    """last (date, ...) with date ≤ d within max_lag_bd business days; None otherwise."""
    lo, hi, best = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= d:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    if series[best][0] < d and bdays(series[best][0], d) > max_lag_bd:
        return None
    return series[best]


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    da = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, mo, da)


def is_target_day(d):
    if d.weekday() >= 5:
        return False
    e = easter(d.year)
    return d not in (date(d.year, 1, 1), e - timedelta(days=2), e + timedelta(days=1), date(d.year, 5, 1),
                     date(d.year, 12, 25), date(d.year, 12, 26))


def quality_ok(q):
    """(emits?, badge) from an acm_g8 QUALITY tag."""
    q = q or ""
    if "NOWCAST" in q:
        return False, "NOWCAST"
    if re.fullmatch(r"ACM_K3_SHORT_SAMPLE_\d+m", q):
        return True, "SHORT"
    if re.fullmatch(r"ACM_K3_\d+m", q):
        try:
            n = int(q.split("_")[-1].rstrip("m"))
        except ValueError:
            return False, "NO_QUAL"
        return (n >= MIN_QUALITY_MONTHS), ("" if n >= MIN_QUALITY_MONTHS else "NO_QUAL")
    return False, "NO_QUAL"


# ─────────────────────────────────────────────────────────────────────────────
# engine
# ─────────────────────────────────────────────────────────────────────────────
def dtp_population(acm, calendar, upto_excl):
    """signed ΔTP22 (bp) for every calendar date d < upto_excl with both window ends available."""
    pop = []
    for i in range(H, len(calendar)):
        d = calendar[i]
        if d >= upto_excl:
            break
        if not acm or calendar[i - H] < acm[0][0]:
            continue
        a1, a0 = asof(acm, d), asof(acm, calendar[i - H])
        if a1 is None or a0 is None or a1[0] <= a0[0]:
            continue
        if not (quality_ok(a1[4])[0] and quality_ok(a0[4])[0]):
            continue
        pop.append((a1[1] - a0[1]) * 100.0)
    return pop


def evaluate_ccy(ccy, t, cal_pos, calendar, acm, nom, y2, be, fx_r, f_series, fx_effective, state_prev):
    """One currency, one session. Returns (row, event) — pure function of its inputs."""
    row = {"ccy": ccy, "t": t.isoformat(), "avail": "OK", "flags": [], "signal": state_prev.get("signal", "OFF"),
           "persist": state_prev.get("persist", 0), "fx_fail_streak": state_prev.get("fx_fail_streak", 0)}
    t0 = calendar[cal_pos - H]
    row["t0"] = t0.isoformat()
    # ── ACM at both ends
    a1, a0 = asof(acm, t), asof(acm, t0)
    if a1 is None or a0 is None or a1[0] <= a0[0]:
        row["avail"] = "NO_DATA"
    else:
        row.update({"acm_asof_t": a1[0].isoformat(), "acm_asof_t0": a0[0].isoformat(),
                    "d_rny": round((a1[2] - a0[2]) * 100.0, 2), "d_tp": round((a1[1] - a0[1]) * 100.0, 2),
                    "tp_t": a1[1], "tp_t0": a0[1], "rny_t": a1[2], "rny_t0": a0[2], "fit_t": a1[3], "fit_t0": a0[3],
                    "quality_t": a1[4], "quality_t0": a0[4]})
        row["d_fit"] = round((a1[3] - a0[3]) * 100.0, 2)
        ok1, b1 = quality_ok(a1[4])
        ok0, b0 = quality_ok(a0[4])
        for b in (b1, b0):
            if b and b not in row["flags"]:
                row["flags"].append(b)
        if not (ok1 and ok0):
            row["avail"] = "NO_QUAL"
    # ── context (never gates)
    n1, n0 = asof(nom, t), asof(nom, t0)
    row["d_nom"] = round((n1[1] - n0[1]) * 100.0, 2) if (n1 and n0 and n1[0] > n0[0]) else None
    row["resid"] = round(row["d_nom"] - row["d_fit"], 2) if (row.get("d_nom") is not None and "d_fit" in row) else None
    q1, q0 = (asof(y2, t), asof(y2, t0)) if y2 else (None, None)
    row["d_2y"] = round((q1[1] - q0[1]) * 100.0, 2) if (q1 and q0 and q1[0] > q0[0]) else None
    e1, e0 = (asof(be, t), asof(be, t0)) if be else (None, None)
    row["d_be"] = round((e1[1] - e0[1]) * 100.0, 2) if (e1 and e0 and e1[0] > e0[0]) else None
    row["context_asof"] = {name: {"t": end[0].isoformat() if end else None,
                                       "t0": start[0].isoformat() if start else None}
                           for name, end, start in (("nominal", n1, n0), ("y2", q1, q0), ("be", e1, e0))}
    # ── FX over the same window (t0, t]: sum of daily log returns on the TARGET calendar
    if fx_effective < t:
        row["flags"].append("DESFASE")
    win = fx_r if ccy != "USD" else f_series
    # Keep t0 fixed: as-of at t never shifts the start of the window backwards.
    end_pos = max((i for i, d in enumerate(calendar[:cal_pos + 1]) if d <= fx_effective), default=-1)
    lo, hi = cal_pos - H + 1, end_pos + 1
    vals = win[lo:hi] if ccy != "CHF" else []
    row["fx_asof_t"] = fx_effective.isoformat()
    row["fx_asof_t0"] = t0.isoformat()
    if ccy == "CHF":
        row["d_fx"] = None
    elif (bdays(fx_effective, t) > MAX_LAG_BD or hi <= lo or len(vals) != hi - lo
          or any(v is None or not math.isfinite(v) for v in vals)):
        row["d_fx"] = None
        if row["avail"] == "OK":
            row["avail"] = "NO_DATA"
    else:
        row["d_fx"] = round(sum(vals) * 100.0, 3)                       # % (log)
    # ── threshold (expanding, up to t−1, signed population)
    pop = dtp_population(acm, calendar, t)
    row["n_pop"] = len(pop)
    if len(pop) < MIN_POP:
        if row["avail"] == "OK":
            row["avail"] = "NO_CAL"
        row["theta"] = row["theta_off"] = row["theta95"] = None
    else:
        sp = sorted(pop)
        row["theta"], row["theta_off"], row["theta95"] = (round(pct(sp, P_ON), 2), round(pct(sp, P_OFF), 2), round(pct(sp, P_HI), 2))
    # ── colour (positive tail only)
    dtp = row.get("d_tp")
    row["colour"] = "white"
    if dtp is not None and row.get("theta") is not None and dtp > 0:
        row["colour"] = "red" if dtp >= row["theta95"] else ("amber" if dtp >= row["theta"] else "white")
    # ── signal axis (only evaluated when availability is OK; DESFASE is OK for the signal)
    event = None
    row["emitter"] = ccy in EMITTERS
    if not row["emitter"]:
        row["signal"] = "NA"
        return row, event
    if row["avail"] not in ("OK",):
        row["signal_note"] = "last %s · data unavailable (%s)" % (state_prev.get("signal", "OFF"), row["avail"])
        return row, event
    prem = (dtp is not None and dtp > 0 and dtp >= row["theta"])
    fx_ok = (row["d_fx"] is not None and row["d_fx"] / 100.0 <= FX_THR)
    prev_sig = state_prev.get("signal", "OFF")
    if prev_sig == "ON":
        streak = 0 if fx_ok else row["fx_fail_streak"] + 1
        if dtp is None or dtp <= 0 or dtp < row["theta_off"] or streak >= FX_FAIL_STREAK:
            row["signal"], row["persist"], row["fx_fail_streak"] = "OFF", 1, 0
        else:
            row["signal"], row["persist"], row["fx_fail_streak"] = "ON", row["persist"] + 1, streak
    else:
        if prem and fx_ok:
            row["signal"], row["persist"], row["fx_fail_streak"] = "ON", 1, 0
            event = {"ccy": ccy, "t": t.isoformat(), "type": "ON", "baseline": False, "d_tp": dtp, "theta": row["theta"], "d_fx": row["d_fx"]}
        else:
            row["signal"], row["persist"], row["fx_fail_streak"] = "OFF", row["persist"] + 1 if prev_sig == "OFF" else 1, 0
    if not state_prev.get("initialized", bool(state_prev) and state_prev.get("avail", "OK") == "OK"):
        event = {"ccy": ccy, "t": t.isoformat(), "type": "BASELINE", "baseline": True,
                 "signal": row["signal"], "d_tp": dtp, "theta": row["theta"], "d_fx": row["d_fx"]}
    return row, event


def load_inputs(data_dir):
    inp = {"acm": {}, "nom": {}, "y2": {}, "be": {}, "sha": {}}
    for c in CCY8:
        p = os.path.join(data_dir, "ACM_G8_%s.csv" % c)
        inp["acm"][c] = read_acm(p)
        if os.path.exists(p):
            inp["sha"]["ACM_G8_%s.csv" % c] = sha256(p)
        inp["nom"][c] = read_series(os.path.join(data_dir, NOM_FILES[c]), col="NOM10")
        inp["y2"][c] = read_series(os.path.join(data_dir, Y2_FILES[c])) if c in Y2_FILES else []
        inp["be"][c] = read_series(os.path.join(data_dir, BE_FILES[c]), col="BE10") if c in BE_FILES else []
    can = os.path.join(data_dir, "usd_factor", "canonical.csv")
    inp["calendar"], inp["f"], inp["r"], inp["desfase"] = read_canonical(can)
    if os.path.exists(can):
        inp["sha"]["usd_factor/canonical.csv"] = sha256(can)
    return inp


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def run_session(t, inp, state, fx_effective, final, data_dir):
    # Full TARGET calendar, including missing publication days; never count CSV gaps as sessions.
    source_dates = inp["calendar"]
    calendar = []
    d = source_dates[0]
    while d <= t:
        if is_target_day(d):
            calendar.append(d)
        d += timedelta(days=1)
    if t not in calendar:
        return None, [], "not a TARGET session"
    cal_pos = calendar.index(t)
    if cal_pos < H + 1:
        return None, [], "calendar too short"
    rows, events = [], []
    for c in CCY8:
        fx_map = dict(zip(source_dates, inp["r"].get(c) or []))
        f_map = dict(zip(source_dates, inp["f"]))
        fx_r = [fx_map.get(d) for d in calendar]
        f_ser = [f_map.get(d) for d in calendar]
        row, ev = evaluate_ccy(c, t, cal_pos, calendar, inp["acm"][c], inp["nom"][c], inp["y2"][c], inp["be"][c],
                               fx_r, f_ser, fx_effective, state.get(c, {}))
        row["state_before"] = state.get(c, {})
        rows.append(row)
        if ev:
            ev.update(acm_asof_t=row.get("acm_asof_t"), flags=row["flags"], signal=row["signal"])
            events.append(ev)
    return rows, events, None


def build_outputs(t, rows, events, inp, fx_effective, final, run_id):
    snap = {"acm_sha": {k: v for k, v in inp["sha"].items() if k.startswith("ACM")}, "canonical_sha": inp["sha"].get("usd_factor/canonical.csv")}
    logrec = {"schema": "s01b-log-1", "version": VERSION, "run_id": run_id, "t": t.isoformat(), "final_run": final,
              "fx_effective": fx_effective.isoformat(), "params": {"P_ON": P_ON, "P_OFF": P_OFF, "P_HI": P_HI, "MIN_POP": MIN_POP,
              "FX_THR": FX_THR, "FX_FAIL_STREAK": FX_FAIL_STREAK, "H": H, "MAX_LAG_BD": MAX_LAG_BD},
              "inputs_sha": inp["sha"], "rows": rows, "events": events}
    out = {"schema": "s01b-1", "version": VERSION, "run_id": run_id, "as_of": t.isoformat(), "fx_effective": fx_effective.isoformat(),
           "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "CONTEXT",
           "note": "compatible con tensión fiscal — CONTEXT, sin voto · umbral expanding a t−1 (P80 firmado, ΔTP>0, histéresis p60) · FX ≤ −0,5 % log sobre la misma ventana · CHF solo lectura",
           "currencies": {r["ccy"]: {k: v for k, v in r.items() if k not in ("state_before",)} for r in rows},
           "events": events, "baseline": any(e.get("baseline") for e in events)}
    new_state = {r["ccy"]: {"signal": r["signal"], "persist": r["persist"], "fx_fail_streak": r["fx_fail_streak"],
                            "avail": r["avail"], "last_eval": t.isoformat(),
                            "initialized": r["avail"] == "OK" or r["state_before"].get("initialized", False)} for r in rows}
    logrec["state_after"] = new_state
    logrec["output"] = out
    logrec["engine_sha256"] = sha256(__file__)
    acm_code = os.path.join(ROOT, "scripts", "acm_g8.py")
    logrec["acm_engine_sha256"] = sha256(acm_code) if os.path.exists(acm_code) else None
    return logrec, out, new_state, snap


def publish(t, logrec, out, new_state, inp, data_dir, dry):
    if dry:
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return
    run_id = logrec["run_id"]
    stage = os.path.join(STAGE, run_id)
    os.makedirs(stage, exist_ok=True)
    # 1. snapshot (staged, then moved as a whole)
    snapdir = os.path.join(stage, "snap")
    os.makedirs(snapdir, exist_ok=True)
    # Full parsed inputs preserve context, exact FX dates and the threshold population.
    def encode(value):
        if isinstance(value, date):
            return {"__date__": value.isoformat()}
        raise TypeError(type(value).__name__)
    input_bytes = json.dumps(inp, default=encode, allow_nan=False).encode()
    with gzip.open(os.path.join(snapdir, "inputs.json.gz"), "wb") as fo:
        fo.write(input_bytes)
    logrec["snapshot_sha256"] = hashlib.sha256(input_bytes).hexdigest()
    shutil.copyfile(__file__, os.path.join(snapdir, "s01b.py"))
    for c in CCY8:
        p = os.path.join(data_dir, "ACM_G8_%s.csv" % c)
        if os.path.exists(p):
            with open(p, "rb") as fi, gzip.open(os.path.join(snapdir, "ACM_G8_%s.csv.gz" % c), "wb") as fo:
                shutil.copyfileobj(fi, fo)
    with gzip.open(os.path.join(snapdir, "calendar.txt.gz"), "wt", encoding="utf-8") as fo:
        fo.write("\n".join(d.isoformat() for d in inp["calendar"]))
    fxw = {}
    for r in logrec["rows"]:
        fxw[r["ccy"]] = {"t0": r["t0"], "t": r["t"], "d_fx": r.get("d_fx")}
    with open(os.path.join(snapdir, "fx_window.json"), "w", encoding="utf-8") as fo:
        json.dump({"fx_effective": logrec["fx_effective"], "windows": fxw, "f_last22": inp["f"][-H:], "r_last22": {c: v[-H:] for c, v in inp["r"].items()}}, fo)
    final_snap = os.path.join(SNAP_DIR, t.isoformat())
    if os.path.exists(final_snap):
        shutil.rmtree(final_snap)
    os.makedirs(SNAP_DIR, exist_ok=True)
    os.replace(snapdir, final_snap)
    # 2. log (write-once)
    atomic_write(os.path.join(LOG_DIR, t.isoformat() + ".json"), json.dumps(logrec, indent=1, ensure_ascii=False))
    # 3. events ledger (append) + state
    if logrec["events"]:
        with open(EVENTS_PATH, "a", encoding="utf-8") as fh:
            for ev in logrec["events"]:
                fh.write(json.dumps(dict(ev, run_id=run_id), ensure_ascii=False) + "\n")
    atomic_write(STATE_PATH, json.dumps({"version": VERSION, "run_id": run_id, "t": t.isoformat(), "currencies": new_state}, indent=1, ensure_ascii=False))
    # 4. public JSON last
    atomic_write(OUT_JSON, json.dumps(out, indent=1, ensure_ascii=False))
    shutil.rmtree(stage, ignore_errors=True)
    log("published %s · run %s · events %d" % (t.isoformat(), run_id, len(logrec["events"])))


def republish_from_log(t):
    """S01B.json out of sync with the session log (partial run) → rebuild it from the log."""
    lg = load_json(os.path.join(LOG_DIR, t.isoformat() + ".json"), None)
    if not lg:
        return
    out = lg["output"]
    atomic_write(STATE_PATH, json.dumps({"version": lg["version"], "run_id": lg["run_id"],
                                       "t": lg["t"], "currencies": lg["state_after"]}, ensure_ascii=False))
    # Rebuild the durable outbox from immutable logs, including any interrupted append.
    all_events = []
    for name in sorted(os.listdir(LOG_DIR)):
        if name.endswith(".json"):
            rec = load_json(os.path.join(LOG_DIR, name), {})
            all_events.extend(dict(e, run_id=rec["run_id"]) for e in rec.get("events", []))
    atomic_write(EVENTS_PATH, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in all_events))
    atomic_write(OUT_JSON, json.dumps(out, indent=1, ensure_ascii=False))
    log("S01B.json rebuilt from log %s" % lg["run_id"])


# ─────────────────────────────────────────────────────────────────────────────
# replay / annex
# ─────────────────────────────────────────────────────────────────────────────
def replay(t_iso):
    t = date.fromisoformat(t_iso)
    lg = load_json(os.path.join(LOG_DIR, t_iso + ".json"), None)
    snap = os.path.join(SNAP_DIR, t_iso)
    if not lg or not os.path.isdir(snap):
        log("replay: no log/snap for %s" % t_iso)
        return 2
    if lg.get("engine_sha256") != sha256(__file__):
        log("replay: engine differs from recorded version; use archived engine")
        return 2
    with gzip.open(os.path.join(snap, "inputs.json.gz"), "rb") as fh:
        payload = fh.read()
    if hashlib.sha256(payload).hexdigest() != lg.get("snapshot_sha256"):
        log("replay: input snapshot hash mismatch")
        return 1
    inp = json.loads(payload, object_hook=lambda x: date.fromisoformat(x["__date__"]) if set(x) == {"__date__"} else x)
    state = {r["ccy"]: r["state_before"] for r in lg["rows"]}
    rows, events, err = run_session(t, inp, state, date.fromisoformat(lg["fx_effective"]), lg["final_run"], DATA)
    if err or rows != lg["rows"] or events != lg["events"]:
        log("replay %s: DIFFERENCES (full rows/events)" % t_iso)
        return 1
    log("replay %s: reproduced exactly (all rows, context and events; SHA-256 checked)" % t_iso)
    return 0


EPISODES = [("abr-2022 USD", "USD", "2022-04-01", "2022-04-30"), ("oct-2023 USD", "USD", "2023-10-01", "2023-10-31"),
            ("abr-2025 USD (fiscal)", "USD", "2025-04-07", "2025-04-30"), ("sep-2022 GBP", "GBP", "2022-09-23", "2022-10-14"),
            ("mar-2020 USD", "USD", "2020-03-09", "2020-03-31"), ("mar-2023 USD", "USD", "2023-03-10", "2023-03-31"),
            ("ago-2024 USD", "USD", "2024-08-01", "2024-08-16"), ("may-2026 JPY", "JPY", "2026-05-01", "2026-05-31"),
            ("ago-2024 JPY (ep. 9)", "JPY", "2024-08-01", "2024-08-23")]


def annex(inp):
    """RETROSPECTIVE replay of the full rule over history (today's ACM loadings). Never a validation."""
    print("=" * 96)
    print("ANEXO §7 · replay RETROSPECTIVO de la regla completa (cargas ACM de hoy) · %s" % VERSION)
    print("=" * 96)
    cal = inp["calendar"]
    hist = {c: [] for c in CCY8}
    state = {}
    for i in range(H + 1, len(cal)):
        t = cal[i]
        if t < date(2021, 9, 1):
            continue
        rows, evs, err = run_session(t, inp, state, t, True, DATA)
        if err:
            continue
        for r in rows:
            hist[r["ccy"]].append(r)
        state = {r["ccy"]: {"signal": r["signal"], "persist": r["persist"], "fx_fail_streak": r["fx_fail_streak"]} for r in rows}
    print("%-24s %-4s %9s %9s %9s %9s   %s" % ("episodio", "CCY", "previstos", "cubiertos", "CTF ON", "NO_CAL", "1er ON"))
    for name, c, d0, d1 in EPISODES:
        a, b = date.fromisoformat(d0), date.fromisoformat(d1)
        expected = sum(1 for d in cal if a <= d <= b)
        w = [r for r in hist[c] if a <= date.fromisoformat(r["t"]) <= b]
        on = [r for r in w if r["signal"] == "ON"]
        nocal = sum(1 for r in w if r["avail"] == "NO_CAL")
        print("%-24s %-4s %9d %9d %9d %9d   %s" % (name, c, expected, len(w), len(on), nocal, on[0]["t"] if on else "—"))
    print("\nENCENDIDOS (OFF→ON) por divisa en toda la historia retrospectiva:")
    for c in EMITTERS:
        ons = [r["t"] for i, r in enumerate(hist[c]) if r["signal"] == "ON" and (i == 0 or hist[c][i - 1]["signal"] != "ON")]
        print("   %s %3d  %s" % (c, len(ons), " ".join(ons[:12]) + (" …" if len(ons) > 12 else "")))
    print("\nEtiqueta: RETROSPECTIVO. ΔTP>0 no equivale a CTF ON. Nada de esto valida la bandera; C lo hará.")


# ─────────────────────────────────────────────────────────────────────────────
def main(argv):
    dry, final = "--dry-run" in argv, "--final" in argv
    if "--replay" in argv:
        return replay(argv[argv.index("--replay") + 1])
    inp = load_inputs(DATA)
    if "--annex" in argv:
        annex(inp)
        return 0
    if not inp["calendar"]:
        log("usd_factor/canonical.csv missing — cannot evaluate (no TARGET calendar / FX)")
        return 0
    today = datetime.now(timezone.utc).date()
    if "--date" in argv:
        today = date.fromisoformat(argv[argv.index("--date") + 1])
    if not is_target_day(today):
        log("%s is not a TARGET business day — no session" % today)
        return 0
    t = today
    logp = os.path.join(LOG_DIR, t.isoformat() + ".json")
    if os.path.exists(logp) and not dry:
        log("session %s already evaluated — one evaluation per session" % t)
        republish_from_log(t)
        return 0
    fx_effective = max(d for d in inp["calendar"] if d <= t)
    if fx_effective < t and not final:
        log("PENDING_FX: last FX %s < session %s — waiting for the ECB reference rates (use --final at end of day)" % (fx_effective, t))
        return 0
    if fx_effective < t and bdays(fx_effective, t) > MAX_LAG_BD:
        log("FX too old (%s) — evaluating with NO_DATA rows" % fx_effective)
    previous = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as fh:
            previous = json.load(fh)  # Corrupt state must not silently reset the baseline.
    if previous.get("t", "") > t.isoformat() and not dry:
        raise ValueError("Refusing to overwrite later session state")
    state = previous.get("currencies", {})
    rows, events, err = run_session(t, inp, state, fx_effective, final, DATA)
    if err:
        log("error: " + err)
        return 0
    run_id = t.isoformat() + "T" + datetime.now(timezone.utc).strftime("%H%M%SZ")
    logrec, out, new_state, _ = build_outputs(t, rows, events, inp, fx_effective, final, run_id)
    publish(t, logrec, out, new_state, inp, DATA, dry)
    for r in rows:
        log("%s avail %-8s signal %-3s dTP %s θ %s dFX %s%%" % (r["ccy"], r["avail"], r["signal"], r.get("d_tp"), r.get("theta"), r.get("d_fx")))
    return 0


if __name__ == "__main__":
    if "--dry-run" in sys.argv or "--annex" in sys.argv or "--replay" in sys.argv:
        sys.exit(main(sys.argv[1:]))
    os.makedirs(S01B, exist_ok=True)
    with open(os.path.join(S01B, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sys.exit(main(sys.argv[1:]))

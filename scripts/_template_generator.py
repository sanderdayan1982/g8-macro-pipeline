#!/usr/bin/env python3
"""
G8 PORT — generator template.  Copy to scripts/generators/<script>_g8.py and fill the
three marked blocks (QUESTION, INPUTS, compute).  Everything else is doctrine and must
not be edited per script.

Doctrine enforced here
  * one script = one question = one file            -> data/state/<script>.json
  * noisy failure, never silence                    -> exceptions + dqm.alerts, never bare except
  * measured percentiles, never assumed             -> pct() computes over the window actually loaded
  * frozen thresholds (Rule 32)                     -> THRESHOLDS dict copied verbatim from the Pine inputs
  * NA = pause, not reset                           -> last-good carry with quality=STALE + age_bd
  * twin-test before funnel (D9)                    -> meta.twin_test.status gates meta.funnel_eligible
  * Gate M + journal columns from day one (D8)     -> gate_m / journal_link filled every run

Python 3.9.6 (Apple).  Stdlib only + pandas/numpy (already in requirements.txt).
Run:  python3 scripts/generators/<script>_g8.py [--asof YYYY-MM-DD] [--dry-run]
"""
import argparse
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
REGISTRY = ROOT / "sources" / "registry.csv"
STATE_DIR = ROOT / "data" / "state"
NORM_DIR = ROOT / "data" / "normalized"
MANUAL = ROOT / "data" / "manual" / "manual_inputs.json"

# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 1 — QUESTION  (fill per script; strings in English, never translated)
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT = "TEMPLATE"                 # IYDT_2Y | IYDT_10Y | CURVA | PSI | XCCY | POS | POL | RISK | RTF10 | FFVA
QUESTION = "One sentence: the single question this script answers."
PINE_VERSION = "vX_Y_Z_EN"          # exact Pine file version being ported
PY_VERSION = "0.1.0"
CUTOFF_DATE = "2024-06-30"          # per-script calibration cut-off (audit finding a). Frozen in gates/GATE_M_prereg.
FORWARD_WINDOWS_BD = [5, 10, 20]

# Frozen thresholds — copy VERBATIM from the Pine `input.*` block.  Any edit requires an acta (Rule 32).
THRESHOLDS = {
    # "z_stress": 2.0,
    # "window_bars": 252,
}

# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 2 — INPUTS  (feed_ids exactly as in sources/registry.csv)
# ─────────────────────────────────────────────────────────────────────────────
INPUT_FEEDS = [
    # "US02Y", "DE02Y", ...
]

# ─────────────────────────────────────────────────────────────────────────────
# Shared machinery — do not edit below per script
# ─────────────────────────────────────────────────────────────────────────────
BD_CALENDARS = {
    # Business-day calendars per country (principle 4).  Holidays live in sources/holidays/<cal>.txt
    # (one ISO date per line).  Weekends always excluded.
}


def load_registry():
    with open(REGISTRY, newline="") as f:
        return {r["feed_id"]: r for r in csv.DictReader(f)}


def holidays_for(cal):
    p = ROOT / "sources" / "holidays" / ("%s.txt" % cal)
    if not p.exists():
        return set()
    return {l.strip() for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")}


def age_bd(data_date, asof, cal):
    """Business days between data_date and asof under the feed's own calendar."""
    hol = holidays_for(cal)
    d0 = pd.Timestamp(data_date)
    d1 = pd.Timestamp(asof)
    if d1 <= d0:
        return 0
    days = pd.bdate_range(d0, d1, inclusive="right")
    return int(sum(1 for d in days if d.strftime("%Y-%m-%d") not in hol))


def load_series(feed_id, reg):
    """Reads data/normalized/<feed_id>.csv  (columns: data_date,value,source_used,quality).
    Falls back to manual_inputs.json for manual feeds.  Returns a DataFrame sorted by date."""
    r = reg[feed_id]
    if r["manual"] == "Y":
        if not MANUAL.exists():
            raise FileNotFoundError("manual_inputs.json missing for %s" % feed_id)
        m = json.loads(MANUAL.read_text()).get(feed_id)
        if m is None:
            raise KeyError("manual input %s not present" % feed_id)
        return pd.DataFrame([{"data_date": m["date"], "value": float(m["value"]),
                              "source_used": "MANUAL", "quality": "MANUAL"}])
    p = NORM_DIR / ("%s.csv" % feed_id)
    if not p.exists():
        raise FileNotFoundError("normalized series missing: %s" % p)
    df = pd.read_csv(p, parse_dates=["data_date"]).sort_values("data_date")
    if df.empty:
        raise ValueError("normalized series empty: %s" % feed_id)
    return df


def plausibility(feed_id, df, reg):
    """Principle 5.  Returns ('OK'|'REJECTED_RANGE'|'REJECTED_JUMP', last_good_row)."""
    r = reg[feed_id]
    last = df.iloc[-1]
    v = float(last["value"])
    lo, hi, jump = (float(r[k]) if r[k] not in ("", None) else None for k in ("plaus_min", "plaus_max", "plaus_max_jump"))
    if lo is not None and hi is not None and not (lo <= v <= hi):
        return "REJECTED_RANGE", df.iloc[-2] if len(df) > 1 else last
    if jump is not None and len(df) > 1:
        prev = float(df.iloc[-2]["value"])
        unit = r["unit"]
        d = abs(v - prev) if unit in ("pct", "pts") else abs(v / prev - 1.0) if prev else 0.0
        if d > jump:
            return "REJECTED_JUMP", df.iloc[-2]
    return "OK", last


def build_input_record(feed_id, reg, asof):
    r = reg[feed_id]
    df = load_series(feed_id, reg)
    plaus, row = plausibility(feed_id, df, reg)
    a = age_bd(row["data_date"], asof, r["calendar"])
    quality = str(row.get("quality", "LIVE"))
    if r["manual"] == "Y":
        exp = int(r["manual_expiry_days"] or 0)
        if exp and (pd.Timestamp(asof) - pd.Timestamp(row["data_date"])).days > exp:
            quality = "MANUAL_EXPIRED"
    elif a > int(r["max_staleness_bd"]):
        quality = "STALE" if a <= 2 * int(r["max_staleness_bd"]) else "DEAD"
    if plaus != "OK":
        quality = "STALE"
    return {
        "feed_id": feed_id, "ccy": r["ccy"], "metric": r["metric"],
        "value": float(row["value"]),
        "data_date": pd.Timestamp(row["data_date"]).strftime("%Y-%m-%d"),
        "age_bd": a, "quality": quality,
        "source_used": str(row.get("source_used", "PRIMARY")),
        "calendar": r["calendar"], "plausibility": plaus,
    }, df


def pct(series, window):
    """Measured percentile of the last value over the last `window` bars.  None if window not filled."""
    s = pd.Series(series).dropna()
    if len(s) < window:
        return None
    w = s.iloc[-window:]
    return float((w < w.iloc[-1]).mean() * 100.0)


def zscore(series, window):
    s = pd.Series(series).dropna()
    if len(s) < window:
        return None
    w = s.iloc[-window:]
    sd = float(w.std(ddof=0))
    return None if sd == 0 else float((w.iloc[-1] - w.mean()) / sd)


def dqm_block(inputs):
    q = [i["quality"] for i in inputs]
    n = len(q)
    w = {"LIVE": 1.0, "PROXY": 0.9, "MANUAL": 0.8, "STALE": 0.5, "MANUAL_EXPIRED": 0.5, "DEAD": 0.0}
    score = 100.0 * sum(w[x] for x in q) / n if n else 0.0
    alerts = []
    for i in inputs:
        if i["quality"] in ("DEAD", "MANUAL_EXPIRED") or i["plausibility"] != "OK":
            alerts.append("%s %s: %s (age %d bd, %s)" % (SCRIPT, i["feed_id"], i["quality"], i["age_bd"], i["plausibility"]))
    return {"health_score": round(score, 1), "n_inputs": n,
            "n_live": q.count("LIVE"), "n_proxy": q.count("PROXY"), "n_manual": q.count("MANUAL"),
            "n_stale": q.count("STALE"), "n_dead": q.count("DEAD"), "n_manual_expired": q.count("MANUAL_EXPIRED"),
            "alerts": alerts}


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode().strip()[:12]
    except Exception:
        return "0000000"


def load_prev_state():
    p = STATE_DIR / ("%s.json" % SCRIPT.lower())
    return json.loads(p.read_text()) if p.exists() else None


def twin_test_meta():
    p = ROOT / "data" / "twin" / ("%s_twin.json" % SCRIPT.lower())
    if not p.exists():
        return {"status": "NOT_STARTED", "started": None, "weeks_elapsed": 0, "state_discrepancy_pct": None, "acta": None}
    return json.loads(p.read_text())


# ─────────────────────────────────────────────────────────────────────────────
# BLOCK 3 — compute  (the Pine logic, line by line; keep Pine variable names)
# ─────────────────────────────────────────────────────────────────────────────
def compute(series, inputs, asof):
    """
    series : dict feed_id -> DataFrame(data_date, value, ...)
    inputs : list of input records (with quality) — use it to emit na_reason when an input is STALE/DEAD
    returns outputs dict matching schemas/state.schema.json#/properties/outputs
    """
    per_ccy = {}
    pairs = {}
    # EXAMPLE (delete):
    # for ccy in ("USD", "EUR"):
    #     s = series["%s02Y" % ccy[:2]]["value"]
    #     per_ccy[ccy] = {
    #         "score": float(s.iloc[-1]),
    #         "state": "NEUTRAL",
    #         "percentile": pct(s, THRESHOLDS["window_bars"]),
    #         "z": zscore(s, THRESHOLDS["window_bars"]),
    #         "window_bars": THRESHOLDS["window_bars"],
    #         "thresholds_frozen": THRESHOLDS,
    #         "components": {},
    #         "na_reason": None,
    #     }
    regime = {"label": "NA", "since": asof, "changed_this_run": False}
    return {"regime": regime, "per_ccy": per_ccy, "pairs": pairs}


# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asof = args.asof

    reg = load_registry()
    missing = [f for f in INPUT_FEEDS if f not in reg]
    if missing:
        raise KeyError("feeds not in registry: %s" % missing)   # noisy failure

    inputs, series = [], {}
    for fid in INPUT_FEEDS:
        rec, df = build_input_record(fid, reg, asof)
        inputs.append(rec)
        series[fid] = df

    outputs = compute(series, inputs, asof)

    prev = load_prev_state()
    if prev:
        prev_regime = prev["outputs"]["regime"]
        outputs["regime"]["changed_this_run"] = prev_regime["label"] != outputs["regime"]["label"]
        outputs["regime"]["since"] = asof if outputs["regime"]["changed_this_run"] else prev_regime["since"]
        diff = [k for k, v in outputs["per_ccy"].items()
                if prev["outputs"]["per_ccy"].get(k, {}).get("state") != v["state"]]
        diff += [k for k, v in outputs["pairs"].items()
                 if prev["outputs"]["pairs"].get(k, {}).get("direction") != v["direction"]]
        if outputs["regime"]["changed_this_run"]:
            diff.append("regime")
    else:
        diff = ["initial"]

    tt = twin_test_meta()
    snapshot = {k: {"state": v["state"], "score": v["score"]} for k, v in outputs["per_ccy"].items()}
    snapshot.update({k: {"direction": v["direction"], "state": v["state"]} for k, v in outputs["pairs"].items()})

    state = {
        "meta": {
            "script": SCRIPT, "question": QUESTION, "pine_version": PINE_VERSION, "py_version": PY_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"), "commit": git_commit(),
            "schema_version": "1.0", "twin_test": tt, "funnel_eligible": tt["status"] == "PASSED",
        },
        "inputs": inputs,
        "outputs": outputs,
        "dqm": dqm_block(inputs),
        "gate_m": {"is_oos": asof > CUTOFF_DATE, "cutoff_date": CUTOFF_DATE,
                   "signal_snapshot": snapshot, "forward_windows_bd": FORWARD_WINDOWS_BD, "realised": None},
        "journal_link": {"state_hash": canonical_hash(outputs),
                         "prev_state_hash": prev["journal_link"]["state_hash"] if prev else None,
                         "diff_vs_prev": diff},
    }

    if args.dry_run:
        print(json.dumps(state, indent=2, default=str))
        return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = STATE_DIR / ("%s.json" % SCRIPT.lower())
    out.write_text(json.dumps(state, indent=2, default=str))
    print("wrote", out, "| health", state["dqm"]["health_score"], "| diff", diff)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
G8 PORT — POS generator  [pos_g8 v0.1.0]
=========================================
Ported from: POS G8 — COT Positioning Monitor [POS-G8 v1.1.3] (07-jul-2026)
Question:    who is already inside the trade?  (speculative positioning: LF/MM vs AM)

This generator does NOT recompute anything: it wraps the output of
scripts/pos_g8_cot_collector.py (v1.0.0, untouched — D10) into the master
state schema (schemas/state.schema.json) and appends the twin-test history.

Inputs   data/pos_g8_cot.json          (written by the collector in the same run)
         sources/registry.csv          (feed CFTC_TFF: staleness, calendar, plausibility)
Outputs  data/state/pos_g8.json        (schema 1.0)
         data/twin/pos_g8_py_history.csv   (data_date, ccy_or_pair, state) — for twin_test.py

Frozen thresholds (Rule 32) are READ from the collector output, not redefined
here, so there is exactly one place where they live.  The generator asserts that
they still equal the values documented in the Pine v1.1.3 header; a mismatch is
a noisy failure, not a silent override.

POS emits NO pair direction: it is a filter/confluence input for the funnel,
not a direction source (D4).  outputs.pairs is therefore empty by design.

Python 3.9+ (Apple 3.9.6 and Actions 3.11).  Stdlib only.
"""
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COT_JSON = ROOT / "data" / "pos_g8_cot.json"
REGISTRY = ROOT / "sources" / "registry.csv"
STATE_OUT = ROOT / "data" / "state" / "pos_g8.json"
TWIN_HIST = ROOT / "data" / "twin" / "pos_g8_py_history.csv"

SCRIPT = "POS"
QUESTION = "Who is already inside the trade? Speculative COT positioning (LF/MM) vs structural (AM), per G8 currency and metals."
PINE_VERSION = "v1_1_3_EN"
PY_VERSION = "0.1.0"
CUTOFF_DATE = "2026-06-19"          # metals calibration 19-jun-2026; fx thresholds are design values (see Gate M 3.1)
FORWARD_WINDOWS_BD = [5, 10, 20]
FEED_ID = "CFTC_TFF"

# Expected frozen thresholds — from the POS-G8 v1.1.3 header. Asserted, not applied.
EXPECTED_THR = {"fx": {"warn": 1.5, "ext": 2.0},
                "XAU": {"warn": 1.68, "ext": 2.29},
                "XAG": {"warn": 1.61, "ext": 2.25}}
EXPECTED_WIN = {"fx": 156, "XAU": 156, "XAG": 104}

# Regime label for the Telegram diff (display-only aggregation; not a Pine output).
REGIME_ORDER = ["NEUTRAL", "STRETCHED", "CROWDED"]


def load_registry_row(feed_id):
    with open(REGISTRY, newline="") as f:
        for r in csv.DictReader(f):
            if r["feed_id"] == feed_id:
                return r
    raise KeyError("feed %s not in sources/registry.csv" % feed_id)


def bdays_between(d0, d1):
    """Business days (Mon-Fri) between two ISO dates; US holidays file optional."""
    hol = set()
    hp = ROOT / "sources" / "holidays" / "US.txt"
    if hp.exists():
        hol = {l.strip() for l in hp.read_text().splitlines() if l.strip() and not l.startswith("#")}
    a, b = date.fromisoformat(d0), date.fromisoformat(d1)
    n = 0
    while a < b:
        a = date.fromordinal(a.toordinal() + 1)
        if a.weekday() < 5 and a.isoformat() not in hol:
            n += 1
    return n


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT)).decode().strip()[:12]
    except Exception:
        return "0000000"


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def twin_meta():
    p = ROOT / "data" / "twin" / "pos_g8_twin.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"status": "NOT_STARTED", "started": None, "weeks_elapsed": 0, "state_discrepancy_pct": None, "acta": None}


def main():
    asof = date.today().isoformat()
    if not COT_JSON.exists():
        raise FileNotFoundError("collector output missing: %s (run scripts/pos_g8_cot_collector.py first)" % COT_JSON)
    cot = json.loads(COT_JSON.read_text())
    reg = load_registry_row(FEED_ID)

    # ── Rule 32 assertion: thresholds and windows must equal the Pine header ──
    if cot.get("thresholds") != EXPECTED_THR or cot.get("windows_weeks") != EXPECTED_WIN:
        raise AssertionError("POS thresholds/windows in collector differ from Pine v1.1.3 — needs an acta (Rule 32). "
                             "collector=%s/%s expected=%s/%s" % (cot.get("thresholds"), cot.get("windows_weeks"),
                                                                   EXPECTED_THR, EXPECTED_WIN))

    # ── input record (provenance) ──
    report_date = cot["report_date"]
    age = bdays_between(report_date, asof)
    max_st = int(reg["max_staleness_bd"])
    quality = "LIVE" if age <= max_st else ("STALE" if age <= 2 * max_st else "DEAD")
    inputs = [{
        "feed_id": FEED_ID, "ccy": "GLOBAL", "metric": "tff",
        "value": float(len(cot["currencies"]) + len(cot["metals"])),
        "data_date": report_date, "age_bd": age, "quality": quality,
        "source_used": "PRIMARY", "calendar": reg["calendar"], "plausibility": "OK",
    }]

    # ── outputs.per_ccy ──
    per_ccy = {}
    for r in cot["currencies"]:
        thr = cot["thresholds"]["fx"]
        per_ccy[r["ccy"]] = {
            "score": r["lf_z"] if r["lf_z"] is not None else 0.0,
            "state": r["state"],
            "percentile": r["lf_pctile"],
            "z": r["lf_z"],
            "window_bars": cot["windows_weeks"]["fx"],
            "thresholds_frozen": {"warn": thr["warn"], "ext": thr["ext"], "z_cap": 3.0},
            "components": {
                "lf_net": r["lf_net"], "lf_cot_index": r["lf_cot_index"], "lf_pct_oi": r["lf_pct_oi"],
                "d_print": r["d_print"], "d_4w": r["d_4w"], "d_13w": r["d_13w"],
                "am_net": r["am_net"], "am_z": r["am_z"],
                "time_in_state": r["time_in_state"], "divergence": r["divergence"],
            },
            "na_reason": None if r["lf_z"] is not None else "lf_z NA (window not filled or feed gap)",
        }
    u = cot["usd"]
    per_ccy["USD"] = {
        "score": u["lf_z"] if u["lf_z"] is not None else 0.0, "state": u["state"],
        "percentile": None, "z": u["lf_z"], "window_bars": cot["windows_weeks"]["fx"],
        "thresholds_frozen": {"warn": EXPECTED_THR["fx"]["warn"], "ext": EXPECTED_THR["fx"]["ext"], "z_cap": 3.0},
        "components": {"lf_net": u["lf_net"], "proxy": True, "note": u["note"]},
        "na_reason": None if u["lf_z"] is not None else "fewer than 4 LF Z available",
    }
    for r in cot["metals"]:
        thr = cot["thresholds"][r["ccy"]]
        per_ccy[r["ccy"]] = {
            "score": r["mm_z"] if r["mm_z"] is not None else 0.0, "state": r["state"],
            "percentile": r["mm_pctile"], "z": r["mm_z"], "window_bars": r["window_weeks"],
            "thresholds_frozen": {"warn": thr["warn"], "ext": thr["ext"], "z_cap": 3.0},
            "components": {"mm_net": r["mm_net"], "mm_cot_index": r["mm_cot_index"], "mm_pct_oi": r["mm_pct_oi"],
                           "d_print": r["d_print"], "d_4w": r["d_4w"], "d_13w": r["d_13w"],
                           "time_in_state": r["time_in_state"]},
            "na_reason": None if r["mm_z"] is not None else "mm_z NA",
        }

    # If the feed is DEAD the states are carried but flagged (NA = pause, not reset)
    if quality == "DEAD":
        for k in per_ccy:
            per_ccy[k]["na_reason"] = "CFTC_TFF DEAD: state carried from %s" % report_date

    states = [v["state"] for v in per_ccy.values()]
    if any(s.startswith("CROWD") for s in states):
        label = "CROWDED"
    elif any(s.startswith("STRETCH") for s in states):
        label = "STRETCHED"
    else:
        label = "NEUTRAL"

    outputs = {"regime": {"label": label, "since": report_date, "changed_this_run": False},
               "per_ccy": per_ccy, "pairs": {}}

    # ── diff vs previous state ──
    prev = json.loads(STATE_OUT.read_text()) if STATE_OUT.exists() else None
    if prev:
        pr = prev["outputs"]["regime"]
        outputs["regime"]["changed_this_run"] = pr["label"] != label
        outputs["regime"]["since"] = report_date if outputs["regime"]["changed_this_run"] else pr["since"]
        diff = [k for k, v in per_ccy.items() if prev["outputs"]["per_ccy"].get(k, {}).get("state") != v["state"]]
        if outputs["regime"]["changed_this_run"]:
            diff.append("regime")
        if prev["inputs"][0]["data_date"] != report_date:
            diff.append("new_report:%s" % report_date)
    else:
        diff = ["initial"]

    alerts = []
    if quality != "LIVE":
        alerts.append("POS CFTC_TFF %s: report %s is %d bd old (max %d)" % (quality, report_date, age, max_st))
    for k, v in per_ccy.items():
        if v["state"] == "NA":
            alerts.append("POS %s state NA" % k)

    tt = twin_meta()
    state = {
        "meta": {"script": SCRIPT, "question": QUESTION, "pine_version": PINE_VERSION, "py_version": PY_VERSION,
                 "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "run_id": os.environ.get("GITHUB_RUN_ID", "local"), "commit": git_commit(),
                 "schema_version": "1.0", "twin_test": tt, "funnel_eligible": tt["status"] == "PASSED"},
        "inputs": inputs,
        "outputs": outputs,
        "dqm": {"health_score": {"LIVE": 100.0, "STALE": 50.0, "DEAD": 0.0}[quality], "n_inputs": 1,
                "n_live": int(quality == "LIVE"), "n_proxy": 0, "n_manual": 0,
                "n_stale": int(quality == "STALE"), "n_dead": int(quality == "DEAD"), "n_manual_expired": 0,
                "alerts": alerts},
        "gate_m": {"is_oos": asof > CUTOFF_DATE, "cutoff_date": CUTOFF_DATE,
                   "signal_snapshot": {k: {"state": v["state"], "score": v["score"]} for k, v in per_ccy.items()},
                   "forward_windows_bd": FORWARD_WINDOWS_BD, "realised": None},
        "journal_link": {"state_hash": canonical_hash(outputs),
                         "prev_state_hash": prev["journal_link"]["state_hash"] if prev else None,
                         "diff_vs_prev": diff},
    }

    STATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    STATE_OUT.write_text(json.dumps(state, indent=2))

    # ── twin-test history: one row per key per report_date (idempotent) ──
    TWIN_HIST.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if TWIN_HIST.exists():
        with open(TWIN_HIST, newline="") as f:
            existing = {(r["data_date"], r["ccy_or_pair"]) for r in csv.DictReader(f)}
    else:
        TWIN_HIST.write_text("data_date,ccy_or_pair,state\n")
    with open(TWIN_HIST, "a", newline="") as f:
        w = csv.writer(f)
        for k, v in per_ccy.items():
            if (report_date, k) not in existing:
                w.writerow([report_date, k, v["state"]])

    print("POS state written: regime=%s report=%s quality=%s diff=%s" % (label, report_date, quality, diff))
    return 0


if __name__ == "__main__":
    sys.exit(main())

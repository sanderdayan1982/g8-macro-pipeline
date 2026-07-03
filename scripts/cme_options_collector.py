#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cme_options_collector.py — v1.1.1 (Gate 1 CLOSED 2026-07-03)
==============================================================
Daily T+1 collector of CME FX options settlements + open interest per strike,
via Databento GLBX.MDP3 (licensed CME distributor). Pure `requests` + stdlib.

v1.1.1: cost-guard and pull windows are CAPPED at the dataset's live
available_end (Databento returns HTTP 422 for any query end beyond it —
bites whenever the target session is yesterday). Found in first Actions run.
v1.1.0: env-parameterized paths (G8_OPT_OUT_DIR / G8_OPT_STATE_FILE) so the
same file runs on macOS (launchd, local redundancy) and GitHub Actions
(primary, repo canonical). osascript notify degrades silently off-macOS.
v1.0.2 fixes (found in June backfill):
  - FRIDAY sessions: OI publishes at next Globex open (Sun night/Mon early),
    not Saturday. Pull window and availability guard now extend to the NEXT
    WEEKDAY (+06:00 / +03:30 UTC). Identical behavior Mon-Thu.
  - CME HOLIDAYS (e.g. Juneteenth): zero settles across all 12 products is
    now recognized as "no session" -> clean skip, not noisy failure.
v1.0.1: HTTP 206 accepted; per-product pulls (504 fix); 5xx retries.

Products (frozen scope, NZD excluded per Gate 0):
  Options : EUU, JPU, GBU, ADU, CAU, CHU   (parent symbology X.OPT)
  Futures : 6E, 6J, 6B, 6A, 6C, 6S         (parent symbology X.FUT)

Data model (verified empirically 2026-07-03):
  - schema `statistics`: stat_type 3 = official settlement (price col),
    stat_type 9 = open interest (quantity col).
  - Settlement of session T publishes ~19:13 UTC of T (stat_flags=2 final).
  - OI of session T publishes ~01:44 UTC of T+1.
  - Grouping key is ts_ref. Pull window: [T 00:00 UTC, T+1 06:00 UTC].
  - schema `definition`: instrument_id -> strike_price, expiration,
    instrument_class (C/P/F).

Guards (noisy failure doctrine):
  availability / cost / schema fingerprint / sanity floors.
  On failure: macOS notification + nonzero exit + no partial files.

Output: OUT_DIR/YYYY-MM-DD/{ROOT}.csv
  session,root,type,symbol,expiry,right,strike,settle,settle_flag,oi

Usage:
  /usr/bin/python3 cme_options_collector.py                     # daily run
  /usr/bin/python3 cme_options_collector.py --session 2026-07-02
"""

import os
import sys
import csv
import io
import json
import time
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("FATAL: requests not installed. /usr/bin/python3 -m pip install requests")

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
BASE = "https://hist.databento.com/v0"
DATASET = "GLBX.MDP3"

OPT_PARENTS = ["EUU.OPT", "JPU.OPT", "GBU.OPT", "ADU.OPT", "CAU.OPT", "CHU.OPT"]
FUT_PARENTS = ["6E.FUT", "6J.FUT", "6B.FUT", "6A.FUT", "6C.FUT", "6S.FUT"]

HOME = Path.home()
# Env overrides let the SAME file run on the Mac (defaults) and in
# GitHub Actions (repo paths): G8_OPT_OUT_DIR / G8_OPT_STATE_FILE.
OUT_DIR = Path(os.environ.get("G8_OPT_OUT_DIR",
    HOME / "Documents" / "G8_options_pipeline" / "canonical"))
STATE_FILE = Path(os.environ.get("G8_OPT_STATE_FILE",
    HOME / "Documents" / "G8_options_pipeline" / "state.json"))
LOG_TAG = "CME-COLLECTOR"

COST_LIMIT_DAY_USD = 0.25
MAX_CATCHUP = 10
AVAILABILITY_MIN_UTC_HOUR = 3.5
RETRIES = 3
BACKOFF_S = [10, 30, 60]

STATS_FINGERPRINT = [
    "ts_recv", "ts_event", "rtype", "publisher_id", "instrument_id",
    "ts_ref", "price", "quantity", "sequence", "ts_in_delta",
    "stat_type", "channel_id", "update_action", "stat_flags", "symbol",
]

STAT_SETTLE = "3"
STAT_OI = "9"

MIN_OPT_SETTLES = 200
MIN_OPT_OI_ROWS = 50
MIN_FUT_SETTLES = 5

# ----------------------------------------------------------------------
# Infrastructure
# ----------------------------------------------------------------------

def get_api_key():
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key:
        keyfile = HOME / ".databento_key"
        if keyfile.exists():
            key = keyfile.read_text().strip()
    if not key:
        fail("No API key: set DATABENTO_API_KEY or create ~/.databento_key")
    return key


def notify(msg):
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "{LOG_TAG}"'],
            timeout=10, check=False,
        )
    except Exception:
        pass


def fail(msg):
    print(f"[{LOG_TAG}] FAIL: {msg}", file=sys.stderr)
    notify(f"FAIL: {msg}"[:200])
    sys.exit(1)


def api_get(auth, endpoint, params):
    """GET with retries. 200 and 206 are both success (206 = streamed)."""
    last_err = ""
    for attempt in range(RETRIES):
        try:
            r = requests.get(f"{BASE}/{endpoint}", params=params,
                             auth=auth, timeout=300)
        except requests.RequestException as e:
            last_err = f"network error: {e}"
            print(f"[{LOG_TAG}] attempt {attempt+1}: {last_err} — retrying")
            time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S)-1)])
            continue
        if r.status_code in (200, 206):
            return r
        if 500 <= r.status_code < 600:
            last_err = f"HTTP {r.status_code}"
            print(f"[{LOG_TAG}] attempt {attempt+1}: {last_err} on "
                  f"{endpoint} — retrying")
            time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S)-1)])
            continue
        fail(f"HTTP {r.status_code} on {endpoint}: {r.text[:300]}")
    fail(f"{endpoint} failed after {RETRIES} attempts ({last_err})")


def parse_db_ts(raw):
    raw = raw.strip().replace("Z", "")
    if "." in raw:
        head, frac = raw.split(".")
        raw = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_success": None}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def prev_weekday(d):
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d

# ----------------------------------------------------------------------
# Guards
# ----------------------------------------------------------------------

def guard_availability(auth, session):
    r = api_get(auth, "metadata.get_dataset_range", {"dataset": DATASET})
    info = r.json()
    end_raw = info.get("end") or info.get("available_end", "")
    end_dt = parse_db_ts(end_raw)
    pub_day = next_weekday(session)   # OI publishes at next Globex open
    required = datetime(pub_day.year, pub_day.month, pub_day.day,
                        tzinfo=timezone.utc) + timedelta(
                            hours=AVAILABILITY_MIN_UTC_HOUR)
    print(f"available_end={end_dt.isoformat()}  required>={required.isoformat()}")
    if end_dt < required:
        print(f"[{LOG_TAG}] Session {session} not yet fully available. "
              f"Clean exit — retry window will catch it.")
        sys.exit(0)
    return end_dt


def guard_cost(auth, session, symbols, schema, end):
    start = str(session)
    r = api_get(auth, "metadata.get_cost", {
        "dataset": DATASET, "schema": schema,
        "symbols": ",".join(symbols), "stype_in": "parent",
        "start": start, "end": end,
    })
    return float(r.text.strip())

# ----------------------------------------------------------------------
# Data pulls (per product — v1.0.1)
# ----------------------------------------------------------------------

def pull_csv_one(auth, schema, parent, start, end):
    r = api_get(auth, "timeseries.get_range", {
        "dataset": DATASET, "schema": schema,
        "symbols": parent, "stype_in": "parent",
        "start": start, "end": end,
        "encoding": "csv", "pretty_px": "true", "pretty_ts": "true",
        "map_symbols": "true",
    })
    return list(csv.DictReader(io.StringIO(r.text)))


def check_stats_fingerprint(rows, parent):
    if not rows:
        fail(f"statistics pull returned zero rows for {parent}")
    cols = list(rows[0].keys())
    if cols != STATS_FINGERPRINT:
        fail(f"SCHEMA CHANGE in statistics ({parent}): {cols}")


def build_definition_map(def_rows, parent):
    need = ["instrument_id", "strike_price", "expiration", "instrument_class"]
    if not def_rows:
        fail(f"definition pull returned zero rows for {parent}")
    cols = set(def_rows[0].keys())
    missing = [c for c in need if c not in cols]
    if missing:
        fail(f"SCHEMA CHANGE in definition ({parent}): missing {missing}")
    m = {}
    for row in def_rows:
        iclass = row["instrument_class"]
        if iclass not in ("C", "P", "F"):
            continue
        expiry = ""
        raw_exp = row.get("expiration", "")
        if raw_exp:
            try:
                expiry = str(parse_db_ts(raw_exp).date())
            except Exception:
                expiry = raw_exp[:10]
        strike = ""
        if iclass in ("C", "P"):
            strike = row.get("strike_price", "")
            if strike and "." in strike:
                strike = strike.rstrip("0").rstrip(".")
        m[row["instrument_id"]] = {
            "strike": strike,
            "expiry": expiry,
            "iclass": iclass,
            "symbol": row.get("symbol") or row.get("raw_symbol", ""),
        }
    return m

# ----------------------------------------------------------------------
# Session processing
# ----------------------------------------------------------------------

def process_session(auth, session):
    print(f"\n===== SESSION {session} =====")
    avail_end = guard_availability(auth, session)

    # Window end: publication day + 6h UTC, CAPPED at the dataset's live
    # available_end (minus a safety minute) — Databento 422s beyond it.
    pub_day = next_weekday(session)
    end_dt = datetime(pub_day.year, pub_day.month, pub_day.day,
                      tzinfo=timezone.utc) + timedelta(hours=6)
    cap = avail_end - timedelta(minutes=1)
    if end_dt > cap:
        end_dt = cap
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    start = str(session)
    session_iso = str(session)

    all_parents = OPT_PARENTS + FUT_PARENTS
    total_cost = 0.0
    for schema in ("statistics", "definition"):
        c = guard_cost(auth, session, all_parents, schema, end)
        print(f"cost[{schema}] = ${c:.4f}")
        total_cost += c
    if total_cost > COST_LIMIT_DAY_USD:
        fail(f"cost guard: ${total_cost:.4f} > ${COST_LIMIT_DAY_USD}")

    products, oi_counts, is_opt_map = {}, {}, {}
    for parent in all_parents:
        root = parent.split(".")[0]
        is_opt = parent.endswith(".OPT")

        stats = pull_csv_one(auth, "statistics", parent, start, end)
        check_stats_fingerprint(stats, parent)
        defs = pull_csv_one(auth, "definition", parent, start, end)
        dmap = build_definition_map(defs, parent)

        settles, ois = {}, {}
        for row in stats:
            if row["ts_ref"][:10] != session_iso:
                continue
            iid = row["instrument_id"]
            if row["stat_type"] == STAT_SETTLE:
                settles[iid] = row
            elif row["stat_type"] == STAT_OI:
                ois[iid] = row

        recs, unknown = [], 0
        for iid, srow in settles.items():
            d = dmap.get(iid)
            if d is None:
                unknown += 1
                continue
            if is_opt and d["iclass"] not in ("C", "P"):
                continue
            if not is_opt and d["iclass"] != "F":
                continue
            recs.append({
                "session": session_iso,
                "root": root,
                "type": "OPT" if is_opt else "FUT",
                "symbol": d["symbol"] or srow["symbol"],
                "expiry": d["expiry"],
                "right": d["iclass"] if is_opt else "",
                "strike": d["strike"],
                "settle": srow["price"],
                "settle_flag": srow["stat_flags"],
                "oi": ois.get(iid, {}).get("quantity", ""),
            })
        n_oi = sum(1 for r in recs if r["oi"] not in ("", None))
        print(f"{root}: settles={len(recs)} oi={n_oi} "
              f"(unknown_defs={unknown})")
        if unknown > 100:
            fail(f"{root}: {unknown} instruments missing definitions — join broken")
        products[root] = recs
        oi_counts[root] = n_oi
        is_opt_map[root] = is_opt

    # Holiday detection BEFORE sanity: zero settles everywhere = no session
    total_settles = sum(len(r) for r in products.values())
    if total_settles == 0:
        print(f"[{LOG_TAG}] {session}: zero settles across all products — "
              f"CME holiday, skipping session cleanly.")
        return "HOLIDAY"

    for root, recs in products.items():
        n_oi = oi_counts[root]
        if is_opt_map[root] and (len(recs) < MIN_OPT_SETTLES
                                 or n_oi < MIN_OPT_OI_ROWS):
            fail(f"sanity: {root} settles={len(recs)} oi={n_oi} below floor")
        if not is_opt_map[root] and len(recs) < MIN_FUT_SETTLES:
            fail(f"sanity: {root} settles={len(recs)} below floor")

    day_dir = OUT_DIR / session_iso
    day_dir.mkdir(parents=True, exist_ok=True)
    header = ["session", "root", "type", "symbol", "expiry",
              "right", "strike", "settle", "settle_flag", "oi"]
    for root, recs in sorted(products.items()):
        recs.sort(key=lambda r: (r["expiry"], r["right"], _fnum(r["strike"])))
        tmp = day_dir / f"{root}.csv.tmp"
        with tmp.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(recs)
        tmp.rename(day_dir / f"{root}.csv")
    print(f"OK: wrote {len(products)} product files to {day_dir}")
    return True


def _fnum(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    auth = (get_api_key(), "")

    if "--session" in sys.argv:
        target = date.fromisoformat(sys.argv[sys.argv.index("--session") + 1])
        process_session(auth, target)
        return

    today = datetime.now(timezone.utc).date()
    target = prev_weekday(today + timedelta(days=1))
    if target >= today:
        target = prev_weekday(today)

    state = load_state()
    last = (date.fromisoformat(state["last_success"])
            if state.get("last_success") else prev_weekday(target))

    pending, cur = [], last
    while cur < target and len(pending) < MAX_CATCHUP:
        cur = next_weekday(cur)
        pending.append(cur)
    if not pending:
        print(f"[{LOG_TAG}] up to date (last_success={last}). Nothing to do.")
        return
    print(f"[{LOG_TAG}] pending sessions: {[str(p) for p in pending]}")

    for sess in pending:
        result = process_session(auth, sess)
        state["last_success"] = str(sess)
        save_state(state)
        notify(f"Session {sess}: "
               f"{'holiday, skipped' if result == 'HOLIDAY' else 'captured OK'}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# =============================================================================
# POS-G8 COT collector  [pos_g8_cot_collector v1.0.1]
# -----------------------------------------------------------------------------
# v1.0.1 (02-sep-2026) — CFTC SODA COLUMN RENAME. The TFF dataset dropped the
#   `_all` suffix on asset_mgr_* and lev_money_* columns (open_interest_all kept).
#   COL map updated; fetch() now retries with the alternate suffix on HTTP 400,
#   and _f() reads either spelling. Error bodies are printed. NO change to any
#   calculation, window or threshold — same source, same series. (G8 PORT D10)
# -----------------------------------------------------------------------------
# Feeds the G8 Macro Pipeline dashboard (g8-institutional.netlify.app) with the
# same COT positioning table produced by the POS-G8 v1.1.3 Pine study, PLUS the
# enrichments the Pine environment cannot compute (COT index, empirical
# percentile, % of open interest, LF/AM divergence, positioning velocity,
# time-in-state).
#
# SOURCE: CFTC Public Reporting Environment, Socrata Open Data API (SODA).
#   Currencies -> TFF (Traders in Financial Futures) Futures-Only  gpe5-46if
#   Metals     -> Disaggregated Futures-Only                       72hh-3qpy
#   Base: https://publicreporting.cftc.gov/resource/<id>.json
#
# WHY DIRECT-FROM-CFTC (vs the TradingView COT mirror the Pine study reads):
#   1. The report is on Socrata Friday evening ET, BEFORE the TradingView mirror
#      ingests it -> the dashboard prints the new week days earlier, and with no
#      dependency on a chart tick, so it also refreshes over the weekend.
#   2. Silver units are native contracts here -> NO x1000 gotcha, so the
#      adaptive /1000 scaling the Pine study needs is unnecessary.
#   3. open_interest_all comes in the same row -> % of OI is free.
#
# ENVIRONMENT: pure stdlib. Runs on Apple /usr/bin/python3 (3.9.6), zero pip.
#
# USAGE:
#   python3 pos_g8_cot_collector.py            # writes ./data/pos_g8_cot.json
#   python3 pos_g8_cot_collector.py --probe    # print available columns & exit
#   python3 pos_g8_cot_collector.py --out path/to/file.json
# =============================================================================

import json
import sys
import urllib.request
import urllib.error
import urllib.parse
import datetime as dt
from pathlib import Path

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
BASE = "https://publicreporting.cftc.gov/resource/"
DS_TFF = "gpe5-46if.json"      # TFF Futures-Only (currencies)
DS_DISAGG = "72hh-3qpy.json"   # Disaggregated Futures-Only (metals)

# CFTC contract-market codes (identical to the POS-G8 Pine hardcodes)
CCY_CODES = {
    "EUR": "099741", "GBP": "096742", "JPY": "097741", "AUD": "232741",
    "NZD": "112741", "CAD": "090741", "CHF": "092741",
}
METAL_CODES = {"XAU": "088691", "XAG": "084691"}

# Rolling windows in WEEKS (Pine used daily bars: 780d ~= 156w, 520d ~= 104w)
WIN = {"fx": 156, "XAU": 156, "XAG": 104}
PULL_WEEKS = 175               # fetch buffer above the largest window

# Classifier thresholds — mirror POS-G8 v1.1.3 exactly
THR = {
    "fx":  {"warn": 1.5,  "ext": 2.0},   # PROVISIONAL
    "XAU": {"warn": 1.68, "ext": 2.29},  # CALIBRATED
    "XAG": {"warn": 1.61, "ext": 2.25},  # CALIBRATED
}
Z_CAP = 3.0
STALE_DAYS = 8                 # CFTC weekly; >8 calendar days since report_date = AGING

# Socrata column names (standard schema). If a pull fails, run --probe: the
# script reports the real columns so you can patch this map in one place.
COL = {
    "date": "report_date_as_yyyy_mm_dd",
    "code": "cftc_contract_market_code",
    "oi":   "open_interest_all",
    "tff": {
        "lf_long": "lev_money_positions_long",
        "lf_short": "lev_money_positions_short",
        "am_long": "asset_mgr_positions_long",
        "am_short": "asset_mgr_positions_short",
    },
    "disagg": {
        "mm_long": "m_money_positions_long_all",
        "mm_short": "m_money_positions_short_all",
    },
}

# ----------------------------------------------------------------------------
# FETCH
# ----------------------------------------------------------------------------
def _get(dataset, params):
    url = BASE + dataset + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "pos-g8/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        print("CFTC HTTP %s on %s\n  BODY: %s" % (e.code, dataset, body), file=sys.stderr)
        raise


def _alt(col):
    """Alternate spelling of a CFTC column: toggle the `_all` suffix."""
    return col[:-4] if col.endswith("_all") else col + "_all"


def fetch(dataset, codes, extra_cols):
    """Return {code: [rows oldest->newest]} for the given contract codes."""
    quoted = ",".join("'%s'" % c for c in codes)
    rows = None
    for attempt, cols in enumerate((extra_cols, [_alt(c) for c in extra_cols])):
        select = ",".join([COL["date"], COL["code"], COL["oi"]] + cols)
        try:
            rows = _get(dataset, {
                "$select": select,
                "$where": "%s in (%s)" % (COL["code"], quoted),
                "$order": COL["date"] + " DESC",
                "$limit": len(codes) * (PULL_WEEKS + 10),
            })
            break
        except urllib.error.HTTPError as e:
            if e.code != 400 or attempt == 1:
                raise
            print("  retrying %s with alternate column names" % dataset, file=sys.stderr)
    out = {c: [] for c in codes}
    for row in rows:
        c = row.get(COL["code"])
        if c in out:
            out[c].append(row)
    for c in out:
        out[c] = list(reversed(out[c]))[-PULL_WEEKS:]   # oldest -> newest
    return out


def probe(dataset):
    rows = _get(dataset, {"$limit": 1})
    cols = sorted(rows[0].keys()) if rows else []
    print("\n%s columns (%d):" % (dataset, len(cols)))
    for c in cols:
        print("  " + c)


# ----------------------------------------------------------------------------
# METRICS
# ----------------------------------------------------------------------------
def _f(row, key):
    v = row.get(key)
    if v is None:
        v = row.get(_alt(key))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def z_capped(series, win):
    """Z of the last value over the trailing window; NA-safe, capped +-Z_CAP."""
    w = [x for x in series[-win:] if x is not None]
    if len(w) < max(30, win // 4) or series[-1] is None:
        return None
    s = stdev(w)
    if not s or s == 0:
        return None
    z = (series[-1] - mean(w)) / s
    return max(min(z, Z_CAP), -Z_CAP)


def cot_index(series, win):
    """Williams-style 0-100 normalization over the trailing window."""
    w = [x for x in series[-win:] if x is not None]
    if len(w) < 10 or series[-1] is None:
        return None
    lo, hi = min(w), max(w)
    if hi == lo:
        return 50.0
    return round(100.0 * (series[-1] - lo) / (hi - lo), 1)


def pct_rank(series, win):
    """Empirical percentile of the last value within its own window (0-100).
    Distribution-free — robust for the non-normal metals series."""
    w = [x for x in series[-win:] if x is not None]
    if len(w) < 10 or series[-1] is None:
        return None
    x = series[-1]
    below = sum(1 for v in w if v <= x)
    return round(100.0 * below / len(w), 1)


def dprint(series):
    """Change vs the previous DISTINCT print (Pine f_dprint). Constant between
    prints, never decays to 0 on a stalled feed."""
    last = prev = None
    for v in series:
        if v is None:
            continue
        if last is None:
            last = v
        elif v != last:
            prev = last
            last = v
    return None if prev is None else last - prev


def delta_n(series, n):
    """Net change over the last n weekly steps (positioning velocity)."""
    vals = [v for v in series if v is not None]
    if len(vals) <= n:
        return None
    return vals[-1] - vals[-1 - n]


def state(z, warn, ext):
    if z is None:
        return "NA"
    if z >= ext:
        return "CROWD LONG"
    if z >= warn:
        return "STRETCH+"
    if z <= -ext:
        return "CROWD SHORT"
    if z <= -warn:
        return "STRETCH-"
    return "NEUTRAL"


def time_in_state(z_series, warn, ext):
    """How many consecutive most-recent prints share the current state."""
    labels = [state(z, warn, ext) for z in z_series if z is not None]
    if not labels:
        return 0
    cur = labels[-1]
    k = 0
    for lab in reversed(labels):
        if lab == cur:
            k += 1
        else:
            break
    return k


def divergence(z_lf, z_am):
    """LF (fast money) vs AM (structural money) disagreement flag."""
    if z_lf is None or z_am is None:
        return None
    opposite = (z_lf > 0) != (z_am > 0)
    return bool(opposite and abs(z_lf) >= 0.5 and abs(z_am) >= 0.5)


def freshness(report_date):
    try:
        d = dt.date.fromisoformat(report_date[:10])
    except Exception:
        return "NA", None
    age = (dt.date.today() - d).days
    return ("FRESH" if age <= STALE_DAYS else "AGING" if age <= 18 else "STALE"), age


# ----------------------------------------------------------------------------
# BUILD ONE ROW
# ----------------------------------------------------------------------------
def build_ccy(name, rows):
    lf = [(_f(r, COL["tff"]["lf_long"]) or 0) - (_f(r, COL["tff"]["lf_short"]) or 0)
          if _f(r, COL["tff"]["lf_long"]) is not None else None for r in rows]
    am = [(_f(r, COL["tff"]["am_long"]) or 0) - (_f(r, COL["tff"]["am_short"]) or 0)
          if _f(r, COL["tff"]["am_long"]) is not None else None for r in rows]
    oi = [_f(r, COL["oi"]) for r in rows]
    win = WIN["fx"]
    warn, ext = THR["fx"]["warn"], THR["fx"]["ext"]

    z_lf = z_capped(lf, win)
    z_am = z_capped(am, win)
    z_lf_hist = [z_capped(lf[:i + 1], win) for i in range(len(lf))]
    net_lf = lf[-1] if lf else None
    net_am = am[-1] if am else None
    oi_now = oi[-1] if oi else None

    return {
        "ccy": name,
        "lf_net": net_lf,
        "lf_z": round(z_lf, 2) if z_lf is not None else None,
        "lf_cot_index": cot_index(lf, win),
        "lf_pctile": pct_rank(lf, win),
        "lf_pct_oi": round(100.0 * net_lf / oi_now, 1) if net_lf is not None and oi_now else None,
        "d_print": dprint(lf),
        "d_4w": delta_n(lf, 4),
        "d_13w": delta_n(lf, 13),
        "am_net": net_am,
        "am_z": round(z_am, 2) if z_am is not None else None,
        "state": state(z_lf, warn, ext),
        "time_in_state": time_in_state(z_lf_hist, warn, ext),
        "divergence": divergence(z_lf, z_am),
        "report_date": rows[-1].get(COL["date"], "")[:10] if rows else None,
    }


def build_metal(name, rows):
    mm = [(_f(r, COL["disagg"]["mm_long"]) or 0) - (_f(r, COL["disagg"]["mm_short"]) or 0)
          if _f(r, COL["disagg"]["mm_long"]) is not None else None for r in rows]
    oi = [_f(r, COL["oi"]) for r in rows]
    win = WIN[name]
    warn, ext = THR[name]["warn"], THR[name]["ext"]

    z = z_capped(mm, win)
    z_hist = [z_capped(mm[:i + 1], win) for i in range(len(mm))]
    net = mm[-1] if mm else None
    oi_now = oi[-1] if oi else None

    return {
        "ccy": name,
        "mm_net": net,
        "mm_z": round(z, 2) if z is not None else None,
        "mm_cot_index": cot_index(mm, win),
        "mm_pctile": pct_rank(mm, win),
        "mm_pct_oi": round(100.0 * net / oi_now, 1) if net is not None and oi_now else None,
        "d_print": dprint(mm),
        "d_4w": delta_n(mm, 4),
        "d_13w": delta_n(mm, 13),
        "state": state(z, warn, ext),
        "time_in_state": time_in_state(z_hist, warn, ext),
        "window_weeks": win,
        "report_date": rows[-1].get(COL["date"], "")[:10] if rows else None,
    }


def usd_proxy(ccy_rows):
    """USD row: -mean of the 7 LF Zs, -sum of the 7 LF nets (desk proxy, '°')."""
    zs = [r["lf_z"] for r in ccy_rows if r["lf_z"] is not None]
    ns = [r["lf_net"] for r in ccy_rows if r["lf_net"] is not None]
    z = -sum(zs) / len(zs) if len(zs) >= 4 else None
    warn, ext = THR["fx"]["warn"], THR["fx"]["ext"]
    return {
        "ccy": "USD",
        "proxy": True,
        "lf_net": -sum(ns) if len(ns) >= 6 else None,
        "lf_z": round(z, 2) if z is not None else None,
        "state": state(z, warn, ext),
        "note": "desk proxy: -mean(7 LF Z)",
    }


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    args = sys.argv[1:]
    if "--probe" in args:
        probe(DS_TFF)
        probe(DS_DISAGG)
        return

    out_path = Path("data/pos_g8_cot.json")
    if "--out" in args:
        out_path = Path(args[args.index("--out") + 1])

    tff = fetch(DS_TFF, list(CCY_CODES.values()),
                list(COL["tff"].values()))
    disagg = fetch(DS_DISAGG, list(METAL_CODES.values()),
                   list(COL["disagg"].values()))

    ccy_rows = [build_ccy(name, tff[code]) for name, code in CCY_CODES.items()]
    metal_rows = [build_metal(name, disagg[code]) for name, code in METAL_CODES.items()]
    usd_row = usd_proxy(ccy_rows)

    report_date = ccy_rows[0]["report_date"] if ccy_rows else None
    status, age = freshness(report_date) if report_date else ("NA", None)

    doc = {
        "schema": "pos_g8_cot/1.0.0",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "CFTC SODA (gpe5-46if TFF, 72hh-3qpy Disaggregated), Futures-Only",
        "report_date": report_date,
        "freshness": {"status": status, "age_days": age, "stale_after_days": STALE_DAYS},
        "thresholds": THR,
        "windows_weeks": WIN,
        "currencies": ccy_rows,
        "usd": usd_row,
        "metals": metal_rows,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2))
    print("wrote %s  (report_date=%s, %s, age=%s d)" %
          (out_path, report_date, status, age))


if __name__ == "__main__":
    main()

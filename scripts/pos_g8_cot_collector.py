#!/usr/bin/env python3
# =============================================================================
# POS-G8 COT collector  [pos_g8_cot_collector v1.1.1]
# -----------------------------------------------------------------------------
# v1.1.1 (14-nov-2026) — ORDEN_ENZO_REVISION-TRIPLE-01 Block 3. If either the
#   long or the short side is missing, that week's net is null. A non-numeric
#   STALE marker is missing and is not filled with 0. A present zero stays
#   zero. Both sides present: long − short, unchanged. No change to windows,
#   thresholds or z.
# -----------------------------------------------------------------------------
# v1.1.0 (17-sep-2026) — P3y / EXT (ACTA_UF1 §E). New per-row fields, NO change
#   to any existing field, window, threshold or state:
#     x = net / open_interest of the SAME report (LF for currencies, MM for
#     metals); p3y = empirical percentile of x_t against the PREVIOUS `win`
#     weekly observations (current excluded; ties count as <=); z3y = z of x_t
#     over the same previous window; ext = p3y <= 10 or >= 90 (two-tail
#     descriptive band, convention — not danger, not reversion). NA while the
#     history is shorter than `win`. Availability follows the CFTC publication
#     (Friday), the same as every other field here.
# -----------------------------------------------------------------------------
# v1.0.1 (02-sep-2026) — CFTC SODA COLUMN RENAME. The TFF dataset dropped the
#   `_all` suffix on asset_mgr_* and lev_money_* columns (open_interest_all kept).
#   COL map updated; fetch() now retries with the alternate suffix on HTTP 400,
#   and _f() reads either spelling. Error bodies are printed. NO change to any
#   calculation, window or threshold — same source, same series. (G8 PORT D10)
# -----------------------------------------------------------------------------
# Feeds the G8 Macro Pipeline dashboard (g8-institutional.netlify.app) with the
# same COT positioning table produced by the POS-G8 Pine study (thresholds: v1.2.6), PLUS the
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
# -----------------------------------------------------------------------------
# v1.1.1 (14-nov-2026) — ORDEN RELOJES-01 A2 (Sander 14-nov-2026 21:47 WAT).
#   The file is written only when the CFTC report date is strictly newer than
#   the date already on disk. The same date writes nothing, including
#   generated_utc. Windows, thresholds and the schema are unchanged.
#   --metals-after-cot recomputes the weekly metals MDP only after that write
#   happened on the Monday 21:05 UTC pass or the Tuesday 09:05 UTC retry.
# -----------------------------------------------------------------------------
# v1.1.2 (15-nov-2026) — FAMILIAS-01 B (REVISION_TRIPLE 14-nov §3.B).
#   The same TFF request (gpe5-46if) also selects dealer positions, dealer
#   trader counts, total traders and the change in open interest. Those
#   values are published only as dealer_shadow on each currency row. They do
#   not enter z, state, thresholds, the USD proxy or any score. A missing
#   side stays null (UNAVAILABLE); it is not replaced with 0. Schema stays
#   pos_g8_cot/1.2.0.
#   Probe 2026-11-15: $select of traders_dealer_spread_all on gpe5-46if
#   returned HTTP 200 (EUR 099741, report_date 2026-11-03). The column is
#   included. Dealer columns keep `_all`; lev_money_* and asset_mgr_* do not.
#   A 400 names one column: only that column is renamed (suffix toggled once)
#   or dropped. The other columns are not rewritten.
# =============================================================================

import json
import re
import subprocess
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

# Classifier thresholds — mirror POS-G8 v1.2.6 exactly (A1, 2026-10-16, consenso Claude + Argon 4).
#   fx      = fallback de Pine (inputs z_warn / z_ext); solo se usa si una divisa no tiene par calibrado.
#   fx_ccy  = f_thr_fx de POS-G8 (CAL 14sep26, calibrate_pos.py v1.0, P10/P90 · P2.5/P97.5 de la LF Z capada,
#             2007-2026). USD = −media de las 7 Z: objeto más estrecho, par propio.
THR = {
    "fx":  {"warn": 1.5,  "ext": 2.0},   # fallback (Pine z_warn / z_ext)
    "fx_ccy": {
        "EUR": {"warn": 1.51, "ext": 2.26}, "GBP": {"warn": 1.47, "ext": 2.06},
        "JPY": {"warn": 1.44, "ext": 2.42}, "AUD": {"warn": 1.46, "ext": 2.30},
        "NZD": {"warn": 1.56, "ext": 2.26}, "CAD": {"warn": 1.51, "ext": 2.36},
        "CHF": {"warn": 1.47, "ext": 2.02}, "USD": {"warn": 0.79, "ext": 1.15},
    },
    "XAU": {"warn": 1.68, "ext": 2.29},  # CALIBRATED
    "XAG": {"warn": 1.61, "ext": 2.25},  # CALIBRATED
}
# AM (Asset Managers) — POS-G8 v1.2.6 [Z2] f_thr_am, SHADOW: umbrales por lado
# [w_neg, e_neg, w_pos, e_pos] = [|P10|, |P2.5|, P90, P97.5] de la AM Z capada (CAL 16-oct-2026, n = 4.497).
# Solo informativo (am_state_shadow): no cambia STATE, ni el libro, ni las alertas.
AM_THR_SHADOW = {
    "EUR": [1.52, 2.12, 2.16, 2.72], "GBP": [2.00, 2.61, 1.41, 2.22], "JPY": [1.48, 1.96, 2.23, 2.76],
    "AUD": [1.79, 2.52, 1.69, 2.44], "NZD": [2.30, 3.00, 1.39, 2.17], "CAD": [1.88, 2.76, 1.69, 2.48],
    "CHF": [2.26, 3.00, 1.16, 1.79], "USD": [0.80, 1.55, 0.91, 1.17],
}
PINE_REF = "POS-G8 v1.2.6"


def fx_thr(ccy):
    t = THR["fx_ccy"].get(ccy) or THR["fx"]
    return t["warn"], t["ext"]
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

# FAMILIAS-01 B. Same TFF $select as COL["tff"], extended. Not a second call.
# traders_dealer_spread_all stayed after the 2026-11-15 probe (HTTP 200).
DEALER_COLS = {
    "long": "dealer_positions_long_all",
    "short": "dealer_positions_short_all",
    "spread": "dealer_positions_spread_all",
    "traders_long": "traders_dealer_long_all",
    "traders_short": "traders_dealer_short_all",
    "traders_spread": "traders_dealer_spread_all",
    "traders_total": "traders_tot_all",
    "oi_change": "change_in_open_interest_all",
}
_REJECTED_COL = re.compile(r"No such column:\s*([A-Za-z0-9_]+)")


def tff_columns():
    """LF/AM columns first, then the dealer columns, for one $select."""
    return list(COL["tff"].values()) + list(DEALER_COLS.values())

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
        body = e.read().decode("utf-8", "replace")
        e.pos_body = body
        print("CFTC HTTP %s on %s\n  BODY: %s" % (e.code, dataset, body[:400]), file=sys.stderr)
        raise


def _alt(col):
    """Alternate spelling of a CFTC column: toggle the `_all` suffix."""
    return col[:-4] if col.endswith("_all") else col + "_all"


def _rejected_column(body):
    """Column named by a Socrata 400, or None when the body does not name one."""
    if not body:
        return None
    match = _REJECTED_COL.search(body)
    return match.group(1) if match else None


def _repair_rejected_column(cols, rejected, tried):
    """Rename or drop only `rejected`. Every other column is copied as it is.

    The `_all` suffix toggles once. A second rejection of that column drops it.
    Returns None when `rejected` is not in this select, so the caller does not
    rewrite the columns it did not name.
    """
    if rejected not in cols:
        return None
    alt = _alt(rejected)
    drop = rejected in tried or alt in tried
    tried.add(rejected)
    tried.add(alt)
    out = []
    for col in cols:
        if col != rejected:
            out.append(col)
        elif not drop:
            out.append(alt)
    return out


def fetch(dataset, codes, extra_cols):
    """Return {code: [rows oldest->newest]} for the given contract codes.

    On HTTP 400, only the column Socrata names is renamed or dropped. The
    other columns keep the spelling that was requested.
    """
    quoted = ",".join("'%s'" % c for c in codes)
    cols = list(extra_cols)
    tried = set()
    rows = None
    while True:
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
            if e.code != 400:
                raise
            rejected = _rejected_column(getattr(e, "pos_body", "") or "")
            repaired = None if not rejected else _repair_rejected_column(cols, rejected, tried)
            if repaired is None or repaired == cols:
                raise
            kept = rejected in repaired or _alt(rejected) in repaired
            print("  retrying %s: %s only %s" % (
                dataset, "rename" if kept else "drop", rejected), file=sys.stderr)
            cols = repaired
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


def dealer_net(long_side, short_side):
    """Long minus short only when both sides are present. A missing side is not zero."""
    if long_side is None or short_side is None:
        return None, "UNAVAILABLE"
    return long_side - short_side, None


def oi_change_state(delta):
    """UP, DOWN or FLAT for a present change. A missing change is UNAVAILABLE."""
    if delta is None:
        return "UNAVAILABLE"
    if delta > 0:
        return "UP"
    if delta < 0:
        return "DOWN"
    return "FLAT"


def dealer_shadow(row):
    """Descriptive dealer block for the latest TFF row.

    Not an input to z, state, the USD proxy, a vote or a score. Trader counts
    stay on each side: CFTC counts a dealer in every side it holds, and this
    block does not add those sides together.
    """
    if row is None:
        row = {}
    long_side = _f(row, DEALER_COLS["long"])
    short_side = _f(row, DEALER_COLS["short"])
    net, net_state = dealer_net(long_side, short_side)
    oi_change = _f(row, DEALER_COLS["oi_change"])
    return {
        "long": long_side,
        "short": short_side,
        "spread": _f(row, DEALER_COLS["spread"]),
        "net": net,
        "net_state": net_state,
        "traders": {
            "long": _f(row, DEALER_COLS["traders_long"]),
            "short": _f(row, DEALER_COLS["traders_short"]),
            "spread": _f(row, DEALER_COLS["traders_spread"]),
        },
        "traders_total": _f(row, DEALER_COLS["traders_total"]),
        "oi_change": oi_change,
        "oi_change_state": oi_change_state(oi_change),
    }


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


def pct_oi_series(net, oi):
    return [(100.0 * n / o) if (n is not None and o) else None for n, o in zip(net, oi)]


def p3y_block(x, win):
    """P3y: percentile of the last x against the PREVIOUS `win` observations
    (current excluded), z on the same window, EXT two-tail band (<=10 / >=90).
    NA unless the previous window is complete (v1.1.0, ACTA_UF1 §E)."""
    if not x or x[-1] is None:
        return {"p3y": None, "z3y": None, "ext": None}
    prev = [v for v in x[-win - 1:-1] if v is not None]
    if len(prev) < win:
        return {"p3y": None, "z3y": None, "ext": None}
    cur = x[-1]
    p = round(100.0 * sum(1 for v in prev if v <= cur) / win, 1)
    sd = stdev(prev)
    z = round((cur - mean(prev)) / sd, 2) if sd else None
    return {"p3y": p, "z3y": z, "ext": bool(p <= 10.0 or p >= 90.0)}


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


def state_asym(z, thr):
    """POS-G8 v1.2.6 f_state_asym (SHADOW): umbrales distintos para el lado corto y el largo."""
    if z is None or not thr:
        return "NA"
    wn, en, wp, ep = thr
    if z >= ep:
        return "CROWD LONG"
    if z >= wp:
        return "STRETCH+"
    if z <= -en:
        return "CROWD SHORT"
    if z <= -wn:
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
def _side_net(row, long_key, short_key):
    """Long minus short. Either side missing → None (null in JSON).

    ORDEN_ENZO_REVISION-TRIPLE-01 Block 3: do not fill a missing side with 0.
    _f already returns None for a non-numeric STALE marker, so STALE is not
    filled either. A present zero is a number and stays in the subtraction.
    """
    lng = _f(row, long_key)
    sht = _f(row, short_key)
    if lng is None or sht is None:
        return None
    return lng - sht


def build_ccy(name, rows):
    lf = [_side_net(r, COL["tff"]["lf_long"], COL["tff"]["lf_short"]) for r in rows]
    am = [_side_net(r, COL["tff"]["am_long"], COL["tff"]["am_short"]) for r in rows]
    oi = [_f(r, COL["oi"]) for r in rows]
    win = WIN["fx"]
    warn, ext = fx_thr(name)

    z_lf = z_capped(lf, win)
    z_am = z_capped(am, win)
    z_lf_hist = [z_capped(lf[:i + 1], win) for i in range(len(lf))]
    p3 = p3y_block(pct_oi_series(lf, oi), win)                           # v1.1.0
    net_lf = lf[-1] if lf else None
    net_am = am[-1] if am else None
    oi_now = oi[-1] if oi else None

    return {
        "ccy": name,
        "lf_net": net_lf,
        "_z_lf": z_lf, "_z_am": z_am,        # A5: Z sin redondear para el USD° (se retiran antes de escribir)
        "lf_z": round(z_lf, 2) if z_lf is not None else None,
        "lf_cot_index": cot_index(lf, win),
        "lf_pctile": pct_rank(lf, win),
        "lf_pct_oi": round(100.0 * net_lf / oi_now, 1) if net_lf is not None and oi_now else None,
        "lf_pctoi_p3y": p3.get("p3y"), "lf_pctoi_z3y": p3.get("z3y"), "lf_ext": p3.get("ext"),   # v1.1.0
        "d_print": dprint(lf),
        "d_4w": delta_n(lf, 4),
        "d_13w": delta_n(lf, 13),
        "am_net": net_am,
        "am_z": round(z_am, 2) if z_am is not None else None,
        "state": state(z_lf, warn, ext),
        "thr": {"warn": warn, "ext": ext},
        "am_state_shadow": state_asym(z_am, AM_THR_SHADOW.get(name)),
        "time_in_state": time_in_state(z_lf_hist, warn, ext),
        "divergence": divergence(z_lf, z_am),
        "report_date": rows[-1].get(COL["date"], "")[:10] if rows else None,
        "dealer_shadow": dealer_shadow(rows[-1] if rows else {}),
    }


def build_metal(name, rows):
    mm = [(_f(r, COL["disagg"]["mm_long"]) or 0) - (_f(r, COL["disagg"]["mm_short"]) or 0)
          if _f(r, COL["disagg"]["mm_long"]) is not None else None for r in rows]
    oi = [_f(r, COL["oi"]) for r in rows]
    win = WIN[name]
    warn, ext = THR[name]["warn"], THR[name]["ext"]

    z = z_capped(mm, win)
    z_hist = [z_capped(mm[:i + 1], win) for i in range(len(mm))]
    p3 = p3y_block(pct_oi_series(mm, oi), win)                           # v1.1.0
    net = mm[-1] if mm else None
    oi_now = oi[-1] if oi else None

    return {
        "ccy": name,
        "mm_net": net,
        "mm_z": round(z, 2) if z is not None else None,
        "mm_cot_index": cot_index(mm, win),
        "mm_pctile": pct_rank(mm, win),
        "mm_pct_oi": round(100.0 * net / oi_now, 1) if net is not None and oi_now else None,
        "mm_pctoi_p3y": p3.get("p3y"), "mm_pctoi_z3y": p3.get("z3y"), "mm_ext": p3.get("ext"),   # v1.1.0
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
    # A5 (2026-10-16): como POS-G8, el USD° se calcula con las Z SIN redondear; con las Z redondeadas a 2 decimales un
    # valor en el umbral (p. ej. 0,79 frente a 0,79) podía clasificar distinto que TradingView.
    zs = [r.get("_z_lf", r["lf_z"]) for r in ccy_rows if r.get("_z_lf", r["lf_z"]) is not None]
    ns = [r["lf_net"] for r in ccy_rows if r["lf_net"] is not None]
    ams = [r.get("_z_am", r.get("am_z")) for r in ccy_rows if r.get("_z_am", r.get("am_z")) is not None]
    z = -sum(zs) / len(zs) if len(zs) >= 4 else None
    z_am = -sum(ams) / len(ams) if len(ams) >= 4 else None
    warn, ext = fx_thr("USD")
    return {
        "ccy": "USD",
        "proxy": True,
        "lf_net": -sum(ns) if len(ns) >= 6 else None,
        "lf_z": round(z, 2) if z is not None else None,
        "state": state(z, warn, ext),
        "thr": {"warn": warn, "ext": ext},
        "am_z": round(z_am, 2) if z_am is not None else None,
        "am_state_shadow": state_asym(z_am, AM_THR_SHADOW["USD"]),
        "note": "desk proxy: -mean(7 LF Z) · AM: -mean(7 AM Z)",
    }


# ----------------------------------------------------------------------------
# WRITE ONLY A NEWER REPORT  (ORDEN RELOJES-01 A2)
# ----------------------------------------------------------------------------
# Monday 21:05 UTC and Tuesday 09:05 UTC. Friday and Saturday stay on
# metals_update.yml; a new Friday report must not recompute the MDP here.
METALS_COT_PASSES = ("5 21 * * 1", "5 9 * * 2")


def parse_report_date(value):
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def one_report_date(rows):
    """Every currency and metal row must carry the same report date.
    A missing date or two dates in one pull is not written."""
    found = []
    for row in rows:
        day = parse_report_date(row.get("report_date") if isinstance(row, dict) else None)
        if day is None:
            who = row.get("ccy") if isinstance(row, dict) else None
            raise ValueError("COT row %s has no report_date; nothing written" % who)
        found.append(day)
    if not found:
        raise ValueError("COT returned no rows; nothing written")
    distinct = sorted(set(found))
    if len(distinct) != 1:
        raise ValueError("mixed COT report dates %s; nothing written" %
                         [d.isoformat() for d in distinct])
    return distinct[0]


def stored_report_date(path):
    """Date already on disk, or None when the file is absent.
    A file that is present but unreadable is an error: it is not replaced."""
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("COT output %s is unreadable; nothing written (%s)" % (path, exc))
    if not isinstance(doc, dict):
        raise ValueError("COT output %s is not a document; nothing written" % path)
    stored = parse_report_date(doc.get("report_date"))
    if stored is None:
        raise ValueError("COT output %s has no report_date; nothing written" % path)
    return stored


def report_is_new(stored, incoming):
    if incoming is None:
        return False
    if stored is None:
        return True
    return incoming > stored


def commit_cot(path, doc):
    """Write doc only when its report date is strictly newer than the file.
    Returns True when the file was written. Any other case leaves the bytes,
    including generated_utc, untouched."""
    if not isinstance(doc, dict):
        raise ValueError("COT document is not an object; nothing written")
    currencies = doc.get("currencies")
    metals = doc.get("metals")
    if not isinstance(currencies, list) or not isinstance(metals, list):
        raise ValueError("COT document is missing currencies or metals; nothing written")
    incoming = one_report_date(currencies + metals)
    header = parse_report_date(doc.get("report_date"))
    if header != incoming:
        raise ValueError("document report_date %s does not match rows %s; nothing written" %
                         (header, incoming.isoformat()))
    stored = stored_report_date(path)
    if not report_is_new(stored, incoming):
        print("COT_RESULT wrote=0 report_date=%s stored=%s" % (
            incoming.isoformat(), stored.isoformat() if stored is not None else ""))
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    print("COT_RESULT wrote=1 report_date=%s" % incoming.isoformat())
    return True


def metals_mdp_command():
    root = Path(__file__).resolve().parents[1]
    return [
        sys.executable,
        str(root / "scripts" / "tools" / "g8step.py"),
        "--name", "metals_fairvalue",
        "--cap", "1080",
        "--",
        sys.executable,
        str(root / "scripts" / "metals_fairvalue_g8.py"),
    ]


def cot_was_written(flag):
    if flag is True or flag == 1:
        return True
    return str(flag).strip().lower() in ("1", "true", "yes")


def metals_mdp_due(schedule, cot_wrote):
    """Weekly metals MDP follows a COT write only on the Monday/Tuesday pass."""
    return cot_was_written(cot_wrote) and str(schedule or "").strip() in METALS_COT_PASSES


def recompute_metals_if_due(schedule, cot_wrote, runner=None):
    """Run the weekly metals model only when metals_mdp_due is true.
    Otherwise no output is opened and generated_at stays as it was."""
    if not metals_mdp_due(schedule, cot_wrote):
        print("METALS_RESULT recomputed=0")
        return False
    if runner is None:
        rc = subprocess.call(metals_mdp_command())
        if rc != 0:
            raise SystemExit(rc)
    else:
        runner()
    print("METALS_RESULT recomputed=1")
    return True


def _opt(args, name):
    if name not in args:
        return ""
    i = args.index(name)
    if i + 1 >= len(args):
        return ""
    return args[i + 1]


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--probe" in args:
        probe(DS_TFF)
        probe(DS_DISAGG)
        return 0
    if "--metals-after-cot" in args:
        recompute_metals_if_due(_opt(args, "--schedule"), _opt(args, "--cot-wrote"))
        return 0

    out_path = Path("data/pos_g8_cot.json")
    if "--out" in args:
        out_path = Path(args[args.index("--out") + 1])

    tff = fetch(DS_TFF, list(CCY_CODES.values()), tff_columns())
    disagg = fetch(DS_DISAGG, list(METAL_CODES.values()),
                   list(COL["disagg"].values()))

    ccy_rows = [build_ccy(name, tff[code]) for name, code in CCY_CODES.items()]
    metal_rows = [build_metal(name, disagg[code]) for name, code in METAL_CODES.items()]
    usd_row = usd_proxy(ccy_rows)
    for r in ccy_rows:
        r.pop("_z_lf", None); r.pop("_z_am", None)

    try:
        report_day = one_report_date(ccy_rows + metal_rows)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    report_date = report_day.isoformat()
    status, age = freshness(report_date)

    doc = {
        "schema": "pos_g8_cot/1.2.0",
        "pine_ref": PINE_REF,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "CFTC SODA (gpe5-46if TFF, 72hh-3qpy Disaggregated), Futures-Only",
        "report_date": report_date,  # CFTC as-of / position date (Tuesday), not the Friday publication
        "freshness": {"status": status, "age_days": age, "stale_after_days": STALE_DAYS},
        "thresholds": THR,
        "am_thresholds_shadow": AM_THR_SHADOW,
        "windows_weeks": WIN,
        "currencies": ccy_rows,
        "usd": usd_row,
        "metals": metal_rows,
    }
    # t331u B4: additive fields only. Schema and existing keys stay 1.2.0.
    import cftc_cadence as _cadence
    doc.update(_cadence.document_fields(report_date))

    try:
        wrote = commit_cot(out_path, doc)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if wrote:
        print("wrote %s  (report_date=%s, %s, age=%s d)" %
              (out_path, report_date, status, age))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fx_futures_collector.py — v1.0.5 (daily mode skips products whose session already has OI on disk — one paid pull per session, not one per cron run) (G8 PORT, Fase 2 / FFVA)
==========================================================
Daily T+1 collector AND one-shot backfill of FX futures settlement, open
interest and cleared volume, contract by contract (principle 7), via Databento.
Same access pattern as cme_options_collector.py v1.1.1 (untouched).

Products (FFVA-G8 v1.3.2 scope — NZD and DX INCLUDED, decision Sander 04-sep-2026):
  GLBX.MDP3  : 6E 6B 6J 6A 6N 6C 6S      (parent symbology X.FUT)
  IFUS.IMPACT: DX                        (ICE US Dollar Index)

Data model (statistics schema): stat_type 3 = settlement (price),
  9 = open interest (quantity), 6 = cleared volume (quantity).
  definition schema: instrument_id -> symbol, expiration, instrument_class F.

Output (long format, one file per root, sorted by session, idempotent):
  data/futures/canonical/{ROOT}.csv
  session,root,symbol,expiry,settle,settle_flag,oi,volume

COST DOCTRINE (D5): every pull is quoted first with metadata.get_cost.
  --backfill START END   quotes, prints the USD amount and STOPS unless --yes.
  daily mode             quotes; refuses above COST_LIMIT_DAY_USD (0.25).

USAGE
  python3 scripts/fx_futures_collector.py                       # yesterday's session (T+1)
  python3 scripts/fx_futures_collector.py --backfill 2025-06-01 2026-09-03          # quote only
  python3 scripts/fx_futures_collector.py --backfill 2025-06-01 2026-09-03 --yes    # download
  python3 scripts/fx_futures_collector.py --session 2026-09-03  # a specific session
"""
import csv
import datetime as dt
import io
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("FATAL: requests not installed (pip install requests)")

BASE = "https://hist.databento.com/v0"
PRODUCTS = {  # root -> (dataset, parent symbol)
    "6E": ("GLBX.MDP3", "6E.FUT"), "6B": ("GLBX.MDP3", "6B.FUT"), "6J": ("GLBX.MDP3", "6J.FUT"),
    "6A": ("GLBX.MDP3", "6A.FUT"), "6N": ("GLBX.MDP3", "6N.FUT"), "6C": ("GLBX.MDP3", "6C.FUT"),
    "6S": ("GLBX.MDP3", "6S.FUT"), "DX": ("IFUS.IMPACT", "DX.FUT"),
}
STAT_SETTLE, STAT_VOLUME, STAT_OI = "3", "6", "9"
COST_LIMIT_DAY_USD = 0.25
RETRIES, BACKOFF_S = 3, [10, 30, 60]
LOG_TAG = "FX-FUT"

ROOT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = Path(os.environ.get("G8_FUT_OUT_DIR", ROOT_DIR / "data" / "futures" / "canonical"))
FIELDS = ["session", "root", "symbol", "expiry", "settle", "settle_flag", "oi", "volume"]


def fail(msg):
    print("[%s] FAIL: %s" % (LOG_TAG, msg), file=sys.stderr)
    sys.exit(1)


def get_api_key():
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key:
        kf = Path.home() / ".databento_key"
        if kf.exists():
            key = kf.read_text().strip()
    if not key:
        fail("No API key: set DATABENTO_API_KEY or create ~/.databento_key")
    return key


def api_get(auth, endpoint, params):
    last = ""
    for attempt in range(RETRIES):
        try:
            r = requests.get("%s/%s" % (BASE, endpoint), params=params, auth=auth, timeout=300)
        except requests.RequestException as e:
            last = "network error: %s" % e
            time.sleep(BACKOFF_S[min(attempt, 2)])
            continue
        if r.status_code in (200, 206):
            return r
        if 500 <= r.status_code < 600:
            last = "HTTP %s" % r.status_code
            time.sleep(BACKOFF_S[min(attempt, 2)])
            continue
        fail("HTTP %s on %s: %s" % (r.status_code, endpoint, r.text[:300]))
    fail("%s failed after %d attempts (%s)" % (endpoint, RETRIES, last))


_RANGE = {}


def available_end(auth, dataset):
    """Databento 422s on any end beyond the dataset's live available_end — cap to it."""
    if dataset not in _RANGE:
        r = api_get(auth, "metadata.get_dataset_range", {"dataset": dataset})
        _RANGE[dataset] = r.json().get("end") or r.json().get("available_end")
    return _RANGE[dataset]


def cap_end(auth, dataset, end_ex):
    ae = available_end(auth, dataset)
    return min(end_ex, ae[:19]) if ae else end_ex


def plus_6h(end_iso):
    """OI of session T publishes ~01:44 UTC of T+1 (Fri sessions at next Globex open): extend window to T+1 06:00."""
    e = dt.datetime.fromisoformat(end_iso[:19]) + dt.timedelta(hours=6)
    return e.isoformat()


def def_day(end_iso):
    """Start of the single day used for the definition schema: the last weekday before `end`."""
    d = dt.date.fromisoformat(end_iso[:10]) - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def quote(auth, dataset, parent, schema, start, end):
    r = api_get(auth, "metadata.get_cost", {"dataset": dataset, "schema": schema, "symbols": parent,
                                            "stype_in": "parent", "start": start, "end": end})
    return float(r.text.strip())


def pull(auth, dataset, parent, schema, start, end):
    r = api_get(auth, "timeseries.get_range", {"dataset": dataset, "schema": schema, "symbols": parent,
                                               "stype_in": "parent", "start": start, "end": end,
                                               "encoding": "csv", "pretty_px": "true", "pretty_ts": "true",
                                               "map_symbols": "true"})
    return list(csv.DictReader(io.StringIO(r.text)))


def defs_map(rows):
    m = {}
    for r in rows:
        if r.get("instrument_class") != "F":
            continue
        m[r["instrument_id"]] = {"symbol": r.get("symbol") or r.get("raw_symbol", ""),
                                 "expiry": (r.get("expiration") or "")[:10]}
    return m


def build_records(root, stats, dmap):
    """(session, instrument) -> record. One row per contract per session."""
    recs = {}
    for r in stats:
        iid, sess = r["instrument_id"], r["ts_ref"][:10]
        d = dmap.get(iid)
        if d is None:
            continue
        k = (sess, iid)
        rec = recs.setdefault(k, {"session": sess, "root": root, "symbol": d["symbol"], "expiry": d["expiry"],
                                  "settle": "", "settle_flag": "", "oi": "", "volume": ""})
        st = r["stat_type"]
        if st == STAT_SETTLE:
            rec["settle"], rec["settle_flag"] = r["price"], r.get("stat_flags", "")
        elif st == STAT_OI:
            rec["oi"] = r["quantity"]
        elif st == STAT_VOLUME:
            rec["volume"] = r["quantity"]
    return [v for v in recs.values() if v["settle"] != "" or v["oi"] != ""]


def merge_write(root, new_recs):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / ("%s.csv" % root)
    old = {}
    if p.exists():
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                old[(r["session"], r["symbol"])] = r
    for r in new_recs:
        k = (r["session"], r["symbol"])
        if k in old:                                   # field-wise: non-empty wins, never overwrite good with empty
            for f in FIELDS:
                if r.get(f) not in ("", None):
                    old[k][f] = r[f]
        else:
            old[k] = dict(r)
    rows = sorted(old.values(), key=lambda r: (r["session"], r["expiry"], r["symbol"]))
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def month_chunks(start, end_ex):
    """[(s, e), ...] monthly slices covering [start, end_ex) — progress visible, partial failure cheap."""
    out, s = [], dt.date.fromisoformat(start[:10])
    e_final = dt.datetime.fromisoformat(end_ex[:19])
    while s.isoformat() < end_ex[:10]:
        nxt = (s.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        e = min(dt.datetime.combine(nxt, dt.time()), e_final)
        out.append((s.isoformat(), e.isoformat()))
        s = nxt
    return out


def have_oi(root, session):
    """True if data/futures/canonical/{root}.csv already holds OI for that session (any contract)."""
    p = OUT_DIR / ("%s.csv" % root)
    if not p.exists():
        return False
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            if r["session"] == session and r.get("oi", "") not in ("", None):
                return True
    return False


def run_range(auth, start, end, yes, cost_cap, products=None, skip_done_session=None):
    """Quote selected products first; download only if approved. Writes per product as it goes."""
    prods = {k: v for k, v in PRODUCTS.items() if not products or k in products}
    if skip_done_session:
        done = [k for k in prods if have_oi(k, skip_done_session)]
        if done:
            print("[%s] session %s already complete for %s — no paid pull" % (LOG_TAG, skip_done_session, ",".join(done)))
        prods = {k: v for k, v in prods.items() if k not in done}
        if not prods:
            return 0
    end_ex = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    total, per, ends = 0.0, {}, {}
    for root, (ds, parent) in prods.items():
        e = cap_end(auth, ds, end_ex)
        ends[root] = e
        d0 = def_day(e)
        cs = quote(auth, ds, parent, "statistics", start, cap_end(auth, ds, plus_6h(e)))
        cd = quote(auth, ds, parent, "definition", d0, e) * len(month_chunks(start, e))
        per[root] = cs + cd
        total += cs + cd
        print("[%s] quote %s (%s) %s -> %s : stats $%.4f + definition $%.4f" % (LOG_TAG, root, ds, start, e[:10], cs, cd), flush=True)
    print("[%s] cost quote %s -> %s : $%.4f TOTAL" % (LOG_TAG, start, end, total))
    if cost_cap is not None and total > cost_cap:
        fail("cost guard: $%.4f > $%.2f" % (total, cost_cap))
    if not yes:
        print("[%s] quote only — re-run with --yes to download." % LOG_TAG)
        return 0
    for root, (ds, parent) in prods.items():
        recs_all = []
        for (cs, ce) in month_chunks(start, ends[root]):
            t0 = time.time()
            ce6 = cap_end(auth, ds, plus_6h(ce))
            # definition of the contracts listed on the FIRST weekday of the chunk (expired ones are still there)
            d0 = cs
            while dt.date.fromisoformat(d0).weekday() >= 5:
                d0 = (dt.date.fromisoformat(d0) + dt.timedelta(days=1)).isoformat()
            d1 = (dt.date.fromisoformat(d0) + dt.timedelta(days=1)).isoformat()
            dmap = defs_map(pull(auth, ds, parent, "definition", d0, min(d1, ce6[:10])))
            stats = pull(auth, ds, parent, "statistics", cs, ce6)
            recs = build_records(root, stats, dmap)
            recs_all += recs
            n = merge_write(root, recs)                 # persist chunk by chunk
            print("[%s] %s %s..%s: %d stat rows -> +%d recs (%.0fs) file=%d" % (
                LOG_TAG, root, cs, ce[:10], len(stats), len(recs), time.time() - t0, n), flush=True)
        n_vol = sum(1 for r in recs_all if r["volume"] != "")
        print("[%s] %s DONE: +%d rows (volume on %d)" % (LOG_TAG, root, len(recs_all), n_vol), flush=True)
    return 0


def main(argv):
    auth = (get_api_key(), "")
    products = None
    if "--products" in argv:
        products = [x.strip().upper() for x in argv[argv.index("--products") + 1].split(",") if x.strip()]
    if "--backfill" in argv:
        i = argv.index("--backfill")
        start, end = argv[i + 1], argv[i + 2]
        return run_range(auth, start, end, "--yes" in argv, cost_cap=None, products=products)
    if "--session" in argv:
        s = argv[argv.index("--session") + 1]
    else:
        d = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        while d.weekday() >= 5:            # T+1 of the last weekday session
            d -= dt.timedelta(days=1)
        s = d.isoformat()
    # daily: quote, cap, pull; OI of session T publishes ~01:44 UTC of T+1 → runs after that
    return run_range(auth, s, s, True, cost_cap=COST_LIMIT_DAY_USD, products=products, skip_done_session=s)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

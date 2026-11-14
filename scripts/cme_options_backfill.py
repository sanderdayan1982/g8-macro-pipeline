#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot Databento backfill of CME FX options canonical sessions.

Reuses scripts/cme_options_collector.py (dataset, parents, statistics fingerprint,
definition join, session window). That file is frozen by SHA-256 and is not modified.

Range (inclusive): 2024-06-01 → 2026-05-31. Databento's `end` is exclusive, so the
metadata and timeseries calls use end=2026-06-01.

Order, always:
  1. metadata.get_cost, metadata.get_record_count, metadata.get_billable_size
     for the WHOLE range and both schemas (statistics, definition).
  2. If the sum of get_cost is greater than 35.00 USD, download nothing and exit
     non-zero. HTTP 402 stops the same way: no partial session is left behind.
  3. Otherwise (and only with --download) write canonical CSVs.

Default is cost-only (--dry-run). A download requires --download.

Origin: each backfilled CSV has a trailing column `origin` with value `backfill`.
Existing readers use csv.DictReader and named columns (cme_fxvol_shadow.read,
build_options_summary.read_rows). An extra trailing column does not move
session,root,type,symbol,expiry,right,strike,settle,settle_flag,oi.
Sessions on or after 2026-06-01 are never written. A session directory that
already contains a CSV is never overwritten.

Credential: environment variable DATABENTO_API_KEY only. No key file, no other secret.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import cme_options_collector as C

START = date(2024, 6, 1)
END_INCLUSIVE = date(2026, 5, 31)
END_EXCLUSIVE = date(2026, 6, 1)          # Databento end is exclusive
PROTECT_FROM = date(2026, 6, 1)           # never overwrite these sessions
CAP_USD = Decimal("35.00")
ORIGIN = "backfill"
SCHEMAS = ("statistics", "definition")
HEADER = ["session", "root", "type", "symbol", "expiry", "right", "strike",
          "settle", "settle_flag", "oi", "origin"]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "options" / "canonical"
DEFAULT_REPORT = ROOT / "data" / "options" / "backfill_report.json"


class PaymentRequired(Exception):
    def __init__(self, endpoint):
        self.endpoint = endpoint
        super().__init__("HTTP 402 on %s" % endpoint)


class CostUnknown(Exception):
    def __init__(self, endpoint):
        self.endpoint = endpoint
        super().__init__("unrecognized numeric body on %s" % endpoint)


def _auth_header(key):
    token = base64.b64encode((key + ":").encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + token}


def urllib_http(method, url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _url(endpoint, params):
    return "%s/%s?%s" % (C.BASE, endpoint, urllib.parse.urlencode(params))


def _symbols():
    return ",".join(C.OPT_PARENTS + C.FUT_PARENTS)


def _range_params(schema):
    return {
        "dataset": C.DATASET,
        "schema": schema,
        "symbols": _symbols(),
        "stype_in": "parent",
        "start": START.isoformat(),
        "end": END_EXCLUSIVE.isoformat(),
    }


def _as_decimal(text, endpoint):
    raw = (text or b"").decode("utf-8", errors="replace").strip()
    if not raw:
        raise CostUnknown(endpoint)
    if raw[0] not in "[{":
        try:
            return Decimal(raw)
        except InvalidOperation:
            raise CostUnknown(endpoint)
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        raise CostUnknown(endpoint)
    if isinstance(val, bool) or not isinstance(val, (int, float, str, dict)):
        raise CostUnknown(endpoint)
    if isinstance(val, dict):
        for key in ("cost", "billable_size", "size", "record_count", "n_records"):
            if key in val and not isinstance(val[key], bool):
                try:
                    return Decimal(str(val[key]))
                except InvalidOperation:
                    raise CostUnknown(endpoint)
        raise CostUnknown(endpoint)
    try:
        return Decimal(str(val))
    except InvalidOperation:
        raise CostUnknown(endpoint)


def metadata_number(http, key, endpoint, params):
    status, _headers, body = http("GET", _url(endpoint, params), _auth_header(key), None)
    if status == 402:
        raise PaymentRequired(endpoint)
    if status not in (200, 206):
        raise PaymentRequired(endpoint) if status == 402 else RuntimeError(
            "HTTP %s on %s" % (status, endpoint))
    return _as_decimal(body, endpoint)


def quote_range(http, key):
    """Whole-range metadata for both schemas. No timeseries call."""
    costs, counts, sizes = {}, {}, {}
    for schema in SCHEMAS:
        params = _range_params(schema)
        costs[schema] = metadata_number(http, key, "metadata.get_cost", params)
        counts[schema] = metadata_number(http, key, "metadata.get_record_count", params)
        sizes[schema] = metadata_number(http, key, "metadata.get_billable_size", params)
    total = sum(costs.values(), Decimal("0"))
    return {"get_cost_usd": {s: str(costs[s]) for s in SCHEMAS} | {"total": str(total)},
            "record_count": {s: str(counts[s]) for s in SCHEMAS},
            "billable_size": {s: str(sizes[s]) for s in SCHEMAS},
            "total": total}


def over_cap(total):
    return Decimal(total) > CAP_USD


def iter_weekdays(start=START, end_inclusive=END_INCLUSIVE):
    day = start
    while day <= end_inclusive:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def session_blocked(out_dir, session):
    """'protected' | 'exists' | None. protected wins even if the directory is empty."""
    if session >= PROTECT_FROM:
        return "protected"
    day = Path(out_dir) / session.isoformat()
    if day.is_dir() and any(p.suffix == ".csv" for p in day.iterdir()):
        return "exists"
    return None


def pull_window(session):
    """Same window as the collector: [session 00:00 UTC, next weekday 06:00 UTC]."""
    pub = C.next_weekday(session)
    end_dt = datetime(pub.year, pub.month, pub.day, tzinfo=timezone.utc) + timedelta(hours=6)
    return session.isoformat(), end_dt.strftime("%Y-%m-%dT%H:%M:%S")


def fetch_csv(http, key, schema, parent, start, end):
    params = {
        "dataset": C.DATASET, "schema": schema, "symbols": parent,
        "stype_in": "parent", "start": start, "end": end,
        "encoding": "csv", "pretty_px": "true", "pretty_ts": "true",
        "map_symbols": "true",
    }
    status, _headers, body = http("GET", _url("timeseries.get_range", params), _auth_header(key), None)
    if status == 402:
        raise PaymentRequired("timeseries.get_range")
    if status not in (200, 206):
        raise RuntimeError("HTTP %s on timeseries.get_range" % status)
    text = body.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def assemble_product(stats, defs, parent, session_iso):
    """Collector join, plus origin=backfill. Does not apply the daily $0.25 guard."""
    C.check_stats_fingerprint(stats, parent)
    dmap = C.build_definition_map(defs, parent)
    root = parent.split(".")[0]
    is_opt = parent.endswith(".OPT")
    settles, ois = {}, {}
    for row in stats:
        if row.get("ts_ref", "")[:10] != session_iso:
            continue
        iid = row["instrument_id"]
        if row["stat_type"] == C.STAT_SETTLE:
            settles[iid] = row
        elif row["stat_type"] == C.STAT_OI:
            ois[iid] = row
    recs, unknown = [], 0
    for iid, srow in settles.items():
        meta = dmap.get(iid)
        if meta is None:
            unknown += 1
            continue
        if is_opt and meta["iclass"] not in ("C", "P"):
            continue
        if not is_opt and meta["iclass"] != "F":
            continue
        recs.append({
            "session": session_iso,
            "root": root,
            "type": "OPT" if is_opt else "FUT",
            "symbol": meta["symbol"] or srow.get("symbol", ""),
            "expiry": meta["expiry"],
            "right": meta["iclass"] if is_opt else "",
            "strike": meta["strike"],
            "settle": srow.get("price", ""),
            "settle_flag": srow.get("stat_flags", ""),
            "oi": ois.get(iid, {}).get("quantity", ""),
            "origin": ORIGIN,
        })
    return recs, unknown, is_opt


def _sanity(root, recs, n_oi, is_opt, unknown):
    if unknown > 100:
        raise RuntimeError("%s: %s instruments missing definitions" % (root, unknown))
    if is_opt and (len(recs) < C.MIN_OPT_SETTLES or n_oi < C.MIN_OPT_OI_ROWS):
        raise RuntimeError("sanity: %s settles=%s oi=%s below floor" % (root, len(recs), n_oi))
    if not is_opt and len(recs) < C.MIN_FUT_SETTLES:
        raise RuntimeError("sanity: %s settles=%s below floor" % (root, len(recs)))


def write_session(out_dir, session, products):
    """Write one session. Returns False when the session must not be touched."""
    reason = session_blocked(out_dir, session)
    if reason:
        return False
    day = Path(out_dir) / session.isoformat()
    tmp = Path(out_dir) / (session.isoformat() + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for root, recs in sorted(products.items()):
            recs = sorted(recs, key=lambda r: (r["expiry"], r["right"], C._fnum(r["strike"])))
            path = tmp / ("%s.csv" % root)
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
                writer.writeheader()
                writer.writerows(recs)
        if day.exists():
            shutil.rmtree(day)
        tmp.rename(day)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return True


def rollback(paths):
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)


def download_sessions(http, key, sessions, out_dir):
    """Download the given sessions. On 402, delete every directory this call created."""
    created = []
    added, skipped, holidays = [], [], []
    try:
        for session in sessions:
            reason = session_blocked(out_dir, session)
            if reason:
                skipped.append({"session": session.isoformat(), "reason": reason})
                continue
            start, end = pull_window(session)
            products = {}
            oi_counts = {}
            opt_map = {}
            unknowns = {}
            for parent in C.OPT_PARENTS + C.FUT_PARENTS:
                stats = fetch_csv(http, key, "statistics", parent, start, end)
                defs = fetch_csv(http, key, "definition", parent, start, end)
                recs, unknown, is_opt = assemble_product(stats, defs, parent, session.isoformat())
                root = parent.split(".")[0]
                products[root] = recs
                oi_counts[root] = sum(1 for r in recs if r["oi"] not in ("", None))
                opt_map[root] = is_opt
                unknowns[root] = unknown
            if sum(len(v) for v in products.values()) == 0:
                holidays.append(session.isoformat())
                continue
            for root, recs in products.items():
                _sanity(root, recs, oi_counts[root], opt_map[root], unknowns[root])
            if write_session(out_dir, session, products):
                created.append(Path(out_dir) / session.isoformat())
                added.append(session.isoformat())
            else:
                skipped.append({"session": session.isoformat(), "reason": session_blocked(out_dir, session)})
    except PaymentRequired:
        rollback(created)
        raise
    except Exception:
        rollback(created)
        raise
    return {"sessions_added": added, "sessions_skipped": skipped, "holidays": holidays}


def _summary(text):
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _report(quote, extra):
    body = {
        "dataset": C.DATASET,
        "stype_in": "parent",
        "symbols": C.OPT_PARENTS + C.FUT_PARENTS,
        "schemas": list(SCHEMAS),
        "start": START.isoformat(),
        "end_inclusive": END_INCLUSIVE.isoformat(),
        "end_exclusive": END_EXCLUSIVE.isoformat(),
        "cap_usd": str(CAP_USD),
        "get_cost_usd": quote["get_cost_usd"],
        "record_count": quote["record_count"],
        "billable_size": quote["billable_size"],
        "actual_cost_usd": None,
        "actual_cost_note": "metadata.get_cost is the estimate. timeseries.get_range does not return a billed amount.",
        "origin_marker": "column origin=backfill appended to canonical CSVs (named-column readers unchanged)",
        "sessions_added": [],
        "sessions_skipped": [],
        "holidays": [],
    }
    body.update(extra)
    return body


def write_report(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def execute(argv=None, http=None, env=None, out_dir=None, report_path=None, sessions=None):
    parser = argparse.ArgumentParser(description="CME options backfill (cost-only unless --download)")
    parser.add_argument("--download", action="store_true",
                        help="download and write canonical files (default is cost-only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="get_cost / record_count / billable_size only (this is the default)")
    args = parser.parse_args(argv)
    dry_run = not args.download
    env = os.environ if env is None else env
    key = (env.get("DATABENTO_API_KEY") or "").strip()
    if not key:
        print("DATABENTO_API_KEY is not set. No metadata call and no download.", file=sys.stderr)
        return 2
    http = http or urllib_http
    out_dir = Path(out_dir or DEFAULT_OUT)
    report_path = Path(report_path or DEFAULT_REPORT)
    try:
        quote = quote_range(http, key)
    except PaymentRequired as exc:
        print("Databento HTTP 402 on %s. Monthly budget exceeded. Nothing downloaded." % exc.endpoint,
              file=sys.stderr)
        return 2
    except CostUnknown as exc:
        print("Could not read a number from %s. Nothing downloaded." % exc.endpoint, file=sys.stderr)
        return 2
    lines = [
        "### Databento backfill quote",
        "- range: %s → %s inclusive (API end %s)" % (START, END_INCLUSIVE, END_EXCLUSIVE),
        "- dataset: %s  stype_in=parent  schemas: %s" % (C.DATASET, ", ".join(SCHEMAS)),
        "- get_cost USD: %s" % quote["get_cost_usd"],
        "- record_count: %s" % quote["record_count"],
        "- billable_size: %s" % quote["billable_size"],
        "- cap: %s USD" % CAP_USD,
    ]
    _summary("\n".join(lines))
    if over_cap(quote["total"]):
        msg = ("HARD CAP: get_cost total %s USD is above %s USD. Download nothing."
               % (quote["total"], CAP_USD))
        print(msg, file=sys.stderr)
        _summary(msg)
        write_report(report_path, _report(quote, {
            "blocked": True, "dry_run": dry_run, "reason": msg,
        }))
        return 3
    if dry_run:
        write_report(report_path, _report(quote, {
            "blocked": False, "dry_run": True,
            "reason": "cost-only default: no timeseries.get_range call",
        }))
        print("dry-run: cost is within the cap. No download.")
        return 0
    target = list(iter_weekdays() if sessions is None else sessions)
    try:
        result = download_sessions(http, key, target, out_dir)
    except PaymentRequired as exc:
        print("Databento HTTP 402 on %s during download. Stopped. "
              "Sessions created by this run were removed. Nothing to commit."
              % exc.endpoint, file=sys.stderr)
        write_report(report_path, _report(quote, {
            "blocked": False, "dry_run": False, "aborted": "HTTP 402",
            "reason": "monthly budget exceeded during download; partial sessions removed",
        }))
        return 2
    except Exception as exc:
        print("download stopped: %s. Sessions created by this run were removed." % type(exc).__name__,
              file=sys.stderr)
        return 1
    write_report(report_path, _report(quote, {
        "blocked": False, "dry_run": False,
        "sessions_added": result["sessions_added"],
        "sessions_skipped": result["sessions_skipped"],
        "holidays": result["holidays"],
    }))
    print("sessions added: %s" % len(result["sessions_added"]))
    return 0


def main(argv=None, **kwargs):
    return execute(argv, **kwargs)


if __name__ == "__main__":
    sys.exit(main())

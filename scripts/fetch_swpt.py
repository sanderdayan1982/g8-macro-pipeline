#!/usr/bin/env python3
"""fetch_swpt.py — Fed central bank liquidity swaps, weekly (H.4.1 / FRED SWPT). t331u B4.

Uses the existing Actions secret FRED_API_KEY by name. The value is never
printed, logged or written. If the key is missing or rejected, falls back
to the public fredgraph CSV (same pattern as real_yields_g8.py).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.request

SERIES = "SWPT"
API = "https://api.stlouisfed.org/fred/series/observations"
GRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv"
OUT_NAME = "SWPT.csv"
UA = {"User-Agent": "G8MesaResearch/1.0 (official FRED/H.4.1 SWPT ingest)"}


def _key():
    return os.environ.get("FRED_API_KEY", "").strip()


def parse_fred_json(text):
    data = json.loads(text)
    out = {}
    for obs in data.get("observations") or []:
        d = (obs.get("date") or "")[:10]
        v = obs.get("value")
        if not d or v in (None, ".", ""):
            continue
        try:
            out[d] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def parse_fredgraph_csv(text):
    out = {}
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return out
    for r in rows[1:]:
        if len(r) < 2:
            continue
        d, v = r[0].strip(), r[1].strip()
        if not d or v in (".", ""):
            continue
        try:
            out[d] = float(v)
        except ValueError:
            continue
    return out


def fred_urls(series=SERIES, start="2002-01-01"):
    """URLs only. The API URL is built without embedding the key here."""
    return {
        "api": "%s?series_id=%s&file_type=json&observation_start=%s" % (API, series, start),
        "graph": "%s?id=%s&cosd=%s" % (GRAPH, series, start),
    }


def fetch_series(opener=None, series=SERIES, start="2002-01-01"):
    """Return (series dict, channel). channel is 'api' or 'fredgraph'. Never logs the key."""
    urls = fred_urls(series, start)
    key = _key()
    if key:
        url = urls["api"] + "&api_key=" + key
        try:
            raw = _get(url, opener)
            parsed = parse_fred_json(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            if parsed:
                return parsed, "api"
            print("::warning title=FRED_API_KEY::respuesta de la API sin observaciones; se usa fredgraph")
        except Exception as e:
            # Do not include the URL (it may carry the key) in the message.
            print("::warning title=FRED_API_KEY::API no usable (%s); se usa fredgraph" % type(e).__name__)
    raw = _get(urls["graph"], opener)
    parsed = parse_fredgraph_csv(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    if not parsed:
        raise RuntimeError("SWPT: sin observaciones (API y fredgraph)")
    return parsed, "fredgraph"


def _get(url, opener):
    if opener:
        return opener(url)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def render(series, source):
    lines = ["Date,Value,Source\n"]
    for d in sorted(series):
        lines.append("%s,%.4f,%s\n" % (d, series[d], source))
    return "".join(lines)


def run(data_dir="data", opener=None):
    series, channel = fetch_series(opener=opener)
    path = os.path.join(data_dir, OUT_NAME)
    os.makedirs(data_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(render(series, "FRED.%s/%s" % (SERIES, channel)))
    os.replace(tmp, path)
    return {"last": max(series), "n": len(series), "channel": channel, "path": path}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ingerir SWPT semanal (H.4.1 / FRED)")
    ap.add_argument("--data-dir", default="data")
    args = ap.parse_args(argv)
    try:
        out = run(data_dir=args.data_dir)
    except Exception as e:
        print("[fetch_swpt] FATAL: %s" % e, file=sys.stderr)
        return 1
    print("[fetch_swpt] OK last=%s n=%d channel=%s" % (out["last"], out["n"], out["channel"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

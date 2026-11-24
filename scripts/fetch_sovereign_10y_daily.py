#!/usr/bin/env python3
"""fetch_sovereign_10y_daily.py — DE 10Y par y ES 10Y diarios (t331u B3).

Series nuevas (no sustituyen a las mensuales IRS.M.* de la Mesa):
  DE: Bundesbank BBSIS, par constante, vencimiento residual 10Y, clave ZAR
      (no el bono corriente WT1010 / BBSSY).
      BBSIS/D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A
  ES: Banco de España ti_1_3.csv, serie D_G0B1F0ZP (alias TI_1_3.11).

Spreads contra DE solo si las dos patas tienen observación de la misma
sesión; si falta una, el diferencial queda en blanco (no 0, no se rellena).
IT 10Y sigue mensual (no hay diario automático verificado). FR TEC10 y
Euribor 3M diario: licencia / Client-Id — no se ingieren aquí (panel privado;
ver acta).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime

DE_URL = (
    "https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
    "D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A"
    "?format=csv&detail=dataonly"
)
# WT1010 is deliberately not used.
FORBIDDEN_DE = ("WT1010", "BBSSY.D.REN.EUR.A630.000000WT1010")
ES_URL = "https://www.bde.es/webbe/es/estadisticas/compartido/datos/csv/ti_1_3.csv"
ES_CODE = "D_G0B1F0ZP"
DE_FILE = "DE_10Y_PAR_DAILY.csv"
ES_FILE = "ES_10Y_DAILY.csv"
SPREAD_FILE = "ES_DE_10Y_SPREAD_DAILY.csv"
UA = {"User-Agent": "G8MesaResearch/1.0 (macro desk; official statistical download)"}


def parse_bbk_number(raw):
    raw = (raw or "").strip()
    if not raw or raw in (".", "-"):
        return None
    return float(raw.replace(",", "."))


def parse_de_csv(content, headers=None):
    """Return (stand_vom, {ISO date: value}). Empty/missing values omitted (not filled)."""
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content
    stand = None
    series = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = next(csv.reader([line], delimiter=";"))
        if not parts:
            continue
        key = parts[0].strip().strip('"')
        if key.lower().startswith("stand vom"):
            stand = parts[1].strip().strip('"') if len(parts) > 1 else ""
            continue
        if key.startswith("20") and len(key) >= 10:
            val = parse_bbk_number(parts[1] if len(parts) > 1 else "")
            if val is not None:
                series[key[:10]] = val
    if headers:
        # capture Last-Modified / filename if present; do not invent dates
        pass
    return stand, series


def parse_es_date(label):
    label = label.strip().strip('"')
    months = {
        "JAN": 1, "ENE": 1, "FEB": 2, "MAR": 3, "APR": 4, "ABR": 4,
        "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "AGO": 8, "SEP": 9,
        "OCT": 10, "NOV": 11, "DEC": 12, "DIC": 12,
    }
    parts = label.replace(",", " ").split()
    if len(parts) != 3:
        return None
    try:
        day = int(parts[0])
        mon = months[parts[1].upper()]
        year = int(parts[2])
        return "%04d-%02d-%02d" % (year, mon, day)
    except (KeyError, ValueError):
        return None


def parse_es_csv(content, code=ES_CODE):
    if isinstance(content, bytes):
        for enc in ("utf-8-sig", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                text = None
        if text is None:
            text = content.decode("latin-1", errors="replace")
    else:
        text = content
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}, None
    header = [c.strip().strip('"') for c in rows[0]]
    if code not in header:
        raise RuntimeError("BdE ti_1_3: no está la serie %s" % code)
    idx = header.index(code)
    series = {}
    source = None
    for r in rows[1:]:
        if not r:
            continue
        label = r[0].strip().strip('"')
        if label.upper().startswith("FUENTE"):
            if len(r) > idx:
                source = r[idx].strip().strip('"') or source
            continue
        if label.upper().startswith("NOTAS") or label.upper().startswith("CÓDIGO") or label.upper().startswith("ALIAS"):
            continue
        if label.upper().startswith("NÚMERO") or label.upper().startswith("DESCRIP") or label.upper().startswith("FRECUENCIA"):
            continue
        day = parse_es_date(label)
        if not day or len(r) <= idx:
            continue
        raw = r[idx].strip().strip('"')
        if not raw or raw == "_":
            continue
        try:
            series[day] = float(raw)
        except ValueError:
            continue
    return series, source


def same_session_spread(left, right):
    """Spread in bp (left − right) only when both sessions exist. Missing → None (blank)."""
    out = {}
    days = set(left) | set(right)
    for d in days:
        if d in left and d in right:
            out[d] = (left[d], right[d], round((left[d] - right[d]) * 100.0, 4))
        else:
            out[d] = (left.get(d), right.get(d), None)
    return out


def render_dv(series, source, extra=None):
    lines = ["Date,Value,Source\n"]
    for d in sorted(series):
        lines.append("%s,%.6f,%s\n" % (d, series[d], source))
    return "".join(lines)


def render_spread(spread, left_name="ES", right_name="DE"):
    lines = ["Date,%s,%s,Spread_bp\n" % (left_name, right_name)]
    for d in sorted(spread):
        lv, rv, sp = spread[d]
        def fmt(x):
            return "" if x is None else ("%.6f" % x if isinstance(x, float) and x == x else str(x))
        # spread blank if either leg missing
        sp_s = "" if sp is None else "%.4f" % sp
        lines.append("%s,%s,%s,%s\n" % (d, fmt(lv), fmt(rv), sp_s))
    return "".join(lines)


def assert_not_wtontherun(url):
    for bad in FORBIDDEN_DE:
        if bad in url:
            raise RuntimeError("prohibido usar el bono corriente %s; usar BBSIS R10XX ZAR" % bad)


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _write_if_changed(path, text):
    """Write only if content changed (t346u: no republicar el mismo feed)."""
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    _write(path, text)
    return True


def load_dv(path):
    """Load a Date,Value[,Source] CSV written by render_dv. Keys are ISO date strings."""
    series = {}
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return series
    for r in rows[1:]:
        if not r or not (r[0] or "").strip():
            continue
        day = r[0].strip()
        if len(day) < 10 or not day[0].isdigit():
            continue
        if len(r) < 2 or not (r[1] or "").strip():
            continue
        try:
            series[day[:10]] = float(r[1])
        except ValueError:
            continue
    return series


def es_already_has_expected(data_dir, now=None, repo_root=None):
    """True if ES already has the observation freshness expects at `now` (t372u)."""
    from datetime import datetime, timezone
    from g8common import freshness as FR

    root = repo_root or os.path.abspath(os.path.join(data_dir, os.pardir))
    rules, params, outputs, cals = FR.load_config(root)
    out = next(o for o in outputs if o["file"] == ES_FILE)
    path = os.path.join(data_dir, ES_FILE)
    have = set()
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            have = FR.valued_obs_dates_from_bytes(fh.read()) or set()
    now = now or datetime.now(timezone.utc)
    ev = FR.evaluate(out, rules[out["rule_id"]], params[out["rule_id"]], cals, now, have)
    exp = ev.get("expected_obs")
    have_max = ev.get("have_max")
    if not exp or not have_max:
        return False, ev
    return have_max >= exp, ev


def http_get(url, opener=None):
    if opener:
        return opener(url)
    import urllib.request
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def run(data_dir="data", opener=None, fetch_de=True, fetch_es=True):
    """Fetch DE and/or ES and refresh the spread.

    fetch_de/fetch_es default True (comportamiento previo). Si una pata no se
    pide, se lee del disco. Un fichero solo se reescribe si el contenido cambia
    (t346u / t372u: la pasada de las 14:00 no republica ES si ya está al día).
    """
    stand = None
    es_src = None
    de_path = os.path.join(data_dir, DE_FILE)
    es_path = os.path.join(data_dir, ES_FILE)
    if fetch_de:
        assert_not_wtontherun(DE_URL)
        stand, de = parse_de_csv(http_get(DE_URL, opener=opener))
        if not de:
            raise RuntimeError("BBSIS R10XX ZAR: sin observaciones")
    else:
        if not os.path.isfile(de_path):
            raise RuntimeError("falta %s para recomponer el spread" % DE_FILE)
        de = load_dv(de_path)
        if not de:
            raise RuntimeError("BBSIS R10XX ZAR: sin observaciones en disco")
    if fetch_es:
        es, es_src = parse_es_csv(http_get(ES_URL, opener=opener))
        if not es:
            raise RuntimeError("BdE D_G0B1F0ZP: sin observaciones")
    else:
        if not os.path.isfile(es_path):
            raise RuntimeError("falta %s para recomponer el spread" % ES_FILE)
        es = load_dv(es_path)
        if not es:
            raise RuntimeError("BdE D_G0B1F0ZP: sin observaciones en disco")
    de_changed = _write_if_changed(de_path, render_dv(de, "BBSIS.R10XX.ZAR")) if fetch_de else False
    es_changed = _write_if_changed(es_path, render_dv(es, "BdE.%s" % ES_CODE)) if fetch_es else False
    spread = same_session_spread(es, de)
    spread_changed = _write_if_changed(os.path.join(data_dir, SPREAD_FILE), render_spread(spread))
    # t347u: procedencia del derivado; el cálculo del spread no cambia.
    _scripts = os.path.dirname(os.path.abspath(__file__))
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    from g8common.derived_provenance import write_for_data_dir
    write_for_data_dir(data_dir, SPREAD_FILE, [DE_FILE, ES_FILE])
    both = sum(1 for d in spread if spread[d][2] is not None)
    blank = sum(1 for d in spread if spread[d][2] is None)
    return {
        "de_last": max(de),
        "es_last": max(es),
        "de_n": len(de),
        "es_n": len(es),
        "stand_vom": stand,
        "es_source": es_src,
        "spread_same_session": both,
        "spread_blank": blank,
        "fetch_de": fetch_de,
        "fetch_es": fetch_es,
        "de_changed": de_changed,
        "es_changed": es_changed,
        "spread_changed": spread_changed,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="DE par y ES 10Y diarios (t331u B3)")
    ap.add_argument("--data-dir", default="data")
    args = ap.parse_args(argv)
    try:
        out = run(data_dir=args.data_dir)
    except Exception as e:
        print("[fetch_sovereign_10y_daily] FATAL: %s" % e, file=sys.stderr)
        return 1
    print("[fetch_sovereign_10y_daily] OK DE last=%s (n=%d, Stand vom %s) ES last=%s (n=%d) spreads same-session=%d blank=%d"
          % (out["de_last"], out["de_n"], out["stand_vom"], out["es_last"], out["es_n"],
             out["spread_same_session"], out["spread_blank"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""fetch_chf_snb1a.py — cubo diario SNB1A.SNB.NSS.KZS.EID → CHF_SPOT_*Y_DAILY (t331u B2).

No sustituye ni reescribe data/CHF_SPOT_<n>Y.csv (cubo mensual rendeiduebd, respaldo
y control de fin de mes). No se toca fetch_chf_snb.py.

Fuente diaria (oficial, cubo de almacén):
  https://data.snb.ch/api/warehouse/cube/SNB1A.SNB.NSS.KZS.EID/data/csv/en
  dimSel explícito: LAUFZEIT(J01M0..J30M0), ZEITPUNKT(A1100), frequency(P1D_L),
  AGGREGATIONSMETHODE(ZZ). Sin J30M0 el volcado no trae el 30Y.

Salidas (no se commitean en este PR):
  data/CHF_SPOT_<n>Y_DAILY.csv     Date,Value,Source,PublishingDate
  data/CHF_CURVE_MONTH_END_CONTROL.csv  comparación a fin de mes vs rendeiduebd
  data/CHF_CURVE_METH_BREAK.json   ruptura de metodología (marca; no reescribe historia)

data.snb.ch rechaza IPs de datacenter: este script está pensado para el Mac
(mismo transporte macfetch que fetch_chf_snb.py). No lanza workflows.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, datetime
from urllib.parse import urlencode

CUBE_ID = "SNB1A.SNB.NSS.KZS.EID"
CUBE_URL = "https://data.snb.ch/api/warehouse/cube/%s/data/csv/en" % CUBE_ID
ZEITPUNKT = "A1100"
FREQUENCY = "P1D_L"
AGG = "ZZ"
YEARLY_TENORS = ["J%02dM0" % i for i in range(1, 31)]
TENOR_YEARS = {t: int(t[1:3]) for t in YEARLY_TENORS}
SPOT_MONTHLY = {y: "CHF_SPOT_%dY.csv" % y for y in (list(range(1, 11)) + [20, 30])}
SPOT_DAILY = {y: "CHF_SPOT_%dY_DAILY.csv" % y for y in range(1, 31)}
CONTROL_NAME = "CHF_CURVE_MONTH_END_CONTROL.csv"
BREAK_NAME = "CHF_CURVE_METH_BREAK.json"
SOURCE = "SNB1A"
BACKUP_SOURCE = "rendeiduebd"
METH_BREAK_ID = "CHF_CURVE_SNB1A_DAILY_2026"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    "Accept": "text/csv,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://data.snb.ch/en",
}


def dimsel(tenors=None):
    tenors = list(tenors or YEARLY_TENORS)
    if "J30M0" not in tenors:
        tenors.append("J30M0")
    return ("LAUFZEIT(%s),ZEITPUNKT(%s),frequency(%s),AGGREGATIONSMETHODE(%s)"
            % (",".join(tenors), ZEITPUNKT, FREQUENCY, AGG))


def cube_url(from_date=None, tenors=None):
    q = {"dimSel": dimsel(tenors)}
    if from_date:
        q["fromDate"] = from_date
    return CUBE_URL + "?" + urlencode(q)


def parse_snb_csv(content):
    """Preamble CubeId/PublishingDate then Date;LAUFZEIT;ZEITPUNKT;frequency;AGGREGATIONSMETHODE;Value."""
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content
    lines = [l for l in text.splitlines() if l.strip()]
    pub = cube = None
    start = 0
    for i, l in enumerate(lines):
        if l.startswith('"CubeId"') or l.startswith("CubeId"):
            cube = l.split(";")[1].strip().strip('"').replace("@", ".")
        if l.startswith('"PublishingDate"') or l.startswith("PublishingDate"):
            pub = l.split(";")[1].strip().strip('"')
        if l.startswith('"Date"') or l.startswith("Date"):
            start = i
            break
    rows = list(csv.reader(lines[start:], delimiter=";"))
    header, body = rows[0], rows[1:]
    return {"cube_id": cube, "publishing_date": pub, "header": header, "body": body}


def rows_to_series(parsed):
    """{tenor: {date: value}} only A1100 / P1D_L / ZZ with a numeric value."""
    header = [h.strip().strip('"') for h in parsed["header"]]
    idx = {name: header.index(name) for name in header}
    out = {}
    for r in parsed["body"]:
        if len(r) < 6:
            continue
        if r[idx.get("ZEITPUNKT", 2)].strip().strip('"') != ZEITPUNKT:
            continue
        if r[idx.get("frequency", 3)].strip().strip('"') != FREQUENCY:
            continue
        if r[idx.get("AGGREGATIONSMETHODE", 4)].strip().strip('"') != AGG:
            continue
        tenor = r[idx.get("LAUFZEIT", 1)].strip().strip('"')
        raw = r[idx.get("Value", 5)].strip().strip('"')
        if not raw:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        day = r[idx.get("Date", 0)].strip().strip('"')
        out.setdefault(tenor, {})[day] = val
    return out


def completeness(series, required=None):
    required = list(required or YEARLY_TENORS)
    missing = [t for t in required if t not in series or not series[t]]
    return missing


def last_observation(series):
    dates = [d for tenor in series.values() for d in tenor]
    return max(dates) if dates else None


def month_end_dates(days):
    """Last calendar date present in each YYYY-MM (the month-end observation available)."""
    by = {}
    for d in days:
        by.setdefault(d[:7], []).append(d)
    return [max(v) for v in by.values()]


def read_dv(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out[r["Date"]] = float(r["Value"])
            except (KeyError, ValueError, TypeError):
                continue
    return out


def render_daily(series, publishing_date, source=SOURCE):
    lines = ["Date,Value,Source,PublishingDate\n"]
    for d in sorted(series):
        lines.append("%s,%.6f,%s,%s\n" % (d, series[d], source, publishing_date or ""))
    return "".join(lines)


def month_end_control(daily_by_year, monthly_by_year):
    """Rows at month-end dates present in both sources. Diff in basis points (daily − monthly).

    No rellena huecos: si falta una pata, esa fecha/tenor no entra (no se imputa).
    """
    rows = []
    years = sorted(set(daily_by_year) | set(monthly_by_year))
    for y in years:
        dmap = daily_by_year.get(y) or {}
        mmap = monthly_by_year.get(y) or {}
        for day in month_end_dates(set(dmap) & set(mmap)):
            dv, mv = dmap[day], mmap[day]
            diff_bp = (dv - mv) * 100.0
            rows.append({
                "Date": day,
                "TenorY": y,
                "SNB1A": round(dv, 6),
                "RENDEIDUEBD": round(mv, 6),
                "Diff_bp": round(diff_bp, 4),
            })
    rows.sort(key=lambda r: (r["Date"], r["TenorY"]))
    return rows


def render_control(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["Date", "TenorY", "SNB1A", "RENDEIDUEBD", "Diff_bp"])
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def methodology_break(last_monthly_date, first_daily_date, publishing_date, last_daily_date, n_tenors, n_control):
    return {
        "id": METH_BREAK_ID,
        "marked": True,
        "history_rewritten": False,
        "backup_cube": BACKUP_SOURCE,
        "daily_cube": CUBE_ID,
        "daily_dimsel": dimsel(),
        "last_rendeiduebd_spot_date": last_monthly_date,
        "first_snb1a_daily_date_in_run": first_daily_date,
        "last_snb1a_daily_date": last_daily_date,
        "publishing_date": publishing_date,
        "tenors_yearly_1y_30y": n_tenors,
        "month_end_control_rows": n_control,
        "note": ("La historia de data/CHF_SPOT_<n>Y.csv (rendeiduebd, publicado mensual) "
                 "no se reescribe. El cubo diario SNB1A se escribe en CHF_SPOT_<n>Y_DAILY.csv. "
                 "A fin de mes se mide la diferencia (SNB1A − rendeiduebd) en pb."),
    }


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def fetch_bytes(url, opener=None):
    if opener:
        return opener(url)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from g8common import macfetch  # noqa: WPS433
    fetch = macfetch.Fetch("chf_snb1a", os.getcwd())
    return fetch.get(url, headers=HEADERS, timeout=120)


def load_monthly(data_dir, years=None):
    years = years or (list(range(1, 11)) + [20, 30])
    out = {}
    for y in years:
        out[y] = read_dv(os.path.join(data_dir, SPOT_MONTHLY.get(y, "CHF_SPOT_%dY.csv" % y)))
    return out


def run(data_dir="data", from_date=None, opener=None):
    url = cube_url(from_date=from_date)
    raw = fetch_bytes(url, opener=opener)
    parsed = parse_snb_csv(raw)
    series = rows_to_series(parsed)
    missing = completeness(series)
    if missing:
        raise RuntimeError("SNB1A incompleto: faltan tenores %s (dimSel debe incluir J30M0)" % ",".join(missing))
    pub = parsed["publishing_date"]
    last = last_observation(series)
    written = []
    daily_by_year = {}
    for tenor, y in TENOR_YEARS.items():
        daily_by_year[y] = series[tenor]
        path = os.path.join(data_dir, SPOT_DAILY[y])
        _write(path, render_daily(series[tenor], pub))
        written.append(os.path.basename(path))
    monthly = load_monthly(data_dir)
    control = month_end_control(daily_by_year, monthly)
    _write(os.path.join(data_dir, CONTROL_NAME), render_control(control))
    last_m = None
    for y, m in monthly.items():
        if m:
            last_m = max(filter(None, [last_m, max(m)]))
    first_d = min(d for tenor in series.values() for d in tenor)
    brk = methodology_break(last_m, first_d, pub, last, len(YEARLY_TENORS), len(control))
    _write(os.path.join(data_dir, BREAK_NAME), json.dumps(brk, indent=2, ensure_ascii=False) + "\n")
    return {
        "publishing_date": pub,
        "last_observation": last,
        "cube_id": parsed["cube_id"],
        "url": url,
        "n_daily_files": len(written),
        "n_control": len(control),
        "break": brk,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Curva diaria SNB1A (t331u B2)")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--from-date", default=None)
    args = ap.parse_args(argv)
    try:
        out = run(data_dir=args.data_dir, from_date=args.from_date)
    except Exception as e:
        print("[fetch_chf_snb1a] FATAL: %s" % e, file=sys.stderr)
        return 1
    print("[fetch_chf_snb1a] OK publishing_date=%s last=%s files=%d control_rows=%d"
          % (out["publishing_date"], out["last_observation"], out["n_daily_files"], out["n_control"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

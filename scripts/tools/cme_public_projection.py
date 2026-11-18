#!/usr/bin/env python3
"""Proyección pública de las tres salidas CME: estado, voto y fecha. Sin cifras.

Lee los tres nombres con cifras (gitignored) y escribe solo
data/public/CME_OPTIONS_SURFACE_public.json, data/public/CME_C1_public.json
y data/public/CME_FXVOL_public.json. No reescribe el origen.
Lo llama daily_update justo antes del commit público. El árbol completo
sigue en el workspace para el push a g8-macro-data.
"""
import json
import os
import sys
from datetime import date

CCYS = ("EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD")


def _date(value):
    if isinstance(value, str) and len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        day = value[:10]
        if day[0:4].isdigit() and day[5:7].isdigit() and day[8:10].isdigit():
            return day
    return None


def _session(doc):
    for key in ("latest_session", "session", "as_of"):
        day = _date(doc.get(key))
        if day:
            return day
    return _date(doc.get("generated_utc"))


def _vote(row, gate):
    if isinstance(gate, dict) and isinstance(gate.get("verdict"), str):
        return gate["verdict"]
    nested = row.get("gate0")
    if isinstance(nested, dict) and isinstance(nested.get("verdict"), str):
        return nested["verdict"]
    if isinstance(nested, str):
        return nested
    return None


def _status(row, latest_row):
    if isinstance(row.get("status"), str):
        return row["status"]
    if isinstance(row.get("map_status"), str):
        return row["map_status"]
    if latest_row:
        return "OK"
    return "NA"


def project(doc):
    if not isinstance(doc, dict):
        doc = {}
    day = _session(doc)
    top = doc.get("status") if isinstance(doc.get("status"), str) else None
    rows = doc.get("currencies") if isinstance(doc.get("currencies"), dict) else {}
    latest = doc.get("latest") if isinstance(doc.get("latest"), dict) else {}
    gate0 = doc.get("gate0") if isinstance(doc.get("gate0"), dict) else {}
    currencies = {}
    any_ok = False
    for ccy in CCYS:
        row = rows.get(ccy) if isinstance(rows.get(ccy), dict) else {}
        lat = latest.get(ccy) if isinstance(latest.get(ccy), dict) else {}
        gate = gate0.get(ccy) if isinstance(gate0.get(ccy), dict) else {}
        status = _status(row, lat)
        if ccy == "NZD" and status == "NA" and not row and not lat:
            status = "NO_CME_OPTIONS"
        if status == "OK":
            any_ok = True
        currencies[ccy] = {
            "status": status,
            "vote": _vote(row, gate),
            "date": day,
        }
    if top is None:
        top = "OK" if any_ok else "UNAVAILABLE"
    out = {"status": top, "as_of": day, "currencies": currencies}
    _reject_numbers(out)
    return out


def _reject_numbers(value):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        sys.stderr.write("cme_public_projection: la salida pública no admite cifras\n")
        sys.exit(2)
    if isinstance(value, dict):
        for item in value.values():
            _reject_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _reject_numbers(item)


def unavailable(doc):
    reason = "datos CME en repo privado"
    as_of = None
    if isinstance(doc, dict):
        if isinstance(doc.get("reason"), str) and doc.get("reason"):
            reason = doc["reason"]
        as_of = _date(doc.get("as_of"))
    if as_of is None:
        as_of = date.today().isoformat()
    return {"status": "UNAVAILABLE", "reason": reason, "as_of": as_of}


def emit(doc):
    if not isinstance(doc, dict) or not doc:
        return unavailable({})
    if doc.get("status") == "UNAVAILABLE" and "currencies" not in doc and "latest" not in doc:
        return unavailable(doc)
    return project(doc)


def main(argv):
    if len(argv) < 4 or (len(argv) - 1) % 3:
        sys.stderr.write("uso: cme_public_projection.py --out DESTINO ORIGEN [--out DESTINO ORIGEN ...]\n")
        return 2
    pairs = []
    i = 1
    while i < len(argv):
        if argv[i] != "--out" or i + 2 >= len(argv):
            sys.stderr.write("uso: cme_public_projection.py --out DESTINO ORIGEN [--out DESTINO ORIGEN ...]\n")
            return 2
        pairs.append((argv[i + 1], argv[i + 2]))
        i += 3
    for dest, src in pairs:
        try:
            with open(src, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            doc = {}
        text = json.dumps(emit(doc), ensure_ascii=False, indent=1) + "\n"
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

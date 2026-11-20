#!/usr/bin/env python3
"""Proyección pública de las tres salidas CME: estado, voto y fecha. Sin cifras.

Lee los tres nombres con cifras (gitignored) y escribe solo
data/public/CME_OPTIONS_SURFACE_public.json, data/public/CME_C1_public.json
y data/public/CME_FXVOL_public.json. No reescribe el origen.
Lo llama daily_update justo antes del commit público. El árbol completo
sigue en el workspace para el push a g8-macro-data.

t275u: en CME_FXVOL_public.json se añaden solo campos de calendario
(generated_utc, fut_roll_window.start/end/active/imm, currencies.*.exp1).
Sin dte1 ni ninguna cifra. El rechazo de cifras sigue activo, con lista
blanca explícita.
"""
import json
import os
import re
import sys
from datetime import date

CCYS = ("EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD")

# Lista blanca del documento público. `reason` ya existía en el camino
# UNAVAILABLE. Los campos nuevos de calendario solo se emiten en FXVOL.
ALLOWED_TOP = frozenset({
    "status",
    "as_of",
    "reason",
    "generated_utc",
    "fut_roll_window",
    "currencies",
})
ALLOWED_ROLL = frozenset({"start", "end", "active", "imm"})
ALLOWED_CURRENCY = frozenset({"status", "vote", "date", "exp1"})

_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _date(value):
    if isinstance(value, str) and len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        day = value[:10]
        if day[0:4].isdigit() and day[5:7].isdigit() and day[8:10].isdigit():
            return day
    return None


def _timestamp(value):
    if isinstance(value, str) and _TIMESTAMP.match(value):
        return value
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


def _roll_window(value):
    if not isinstance(value, dict):
        return None
    active = value.get("active")
    return {
        "start": _date(value.get("start")),
        "end": _date(value.get("end")),
        "active": active if isinstance(active, bool) else None,
        "imm": _date(value.get("imm")),
    }


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


def _reject_disallowed(value, where):
    """Falla si aparece un campo fuera de la lista blanca o un tipo no admitido."""
    if where == "top":
        if not isinstance(value, dict):
            sys.stderr.write("cme_public_projection: documento público no válido\n")
            sys.exit(2)
        extra = sorted(set(value) - ALLOWED_TOP)
        if extra:
            sys.stderr.write("cme_public_projection: campo no permitido: %s\n" % extra[0])
            sys.exit(2)
        if "as_of" in value and value["as_of"] is not None and _date(value["as_of"]) is None:
            sys.stderr.write("cme_public_projection: as_of no es una fecha\n")
            sys.exit(2)
        if "generated_utc" in value and value["generated_utc"] is not None and _timestamp(value["generated_utc"]) is None:
            sys.stderr.write("cme_public_projection: generated_utc no es un timestamp ISO\n")
            sys.exit(2)
        if "fut_roll_window" in value and value["fut_roll_window"] is not None:
            _reject_disallowed(value["fut_roll_window"], "roll")
        if "currencies" in value and value["currencies"] is not None:
            if not isinstance(value["currencies"], dict):
                sys.stderr.write("cme_public_projection: currencies no válido\n")
                sys.exit(2)
            for row in value["currencies"].values():
                _reject_disallowed(row, "currency")
        return
    if where == "roll":
        if not isinstance(value, dict):
            sys.stderr.write("cme_public_projection: fut_roll_window no válido\n")
            sys.exit(2)
        extra = sorted(set(value) - ALLOWED_ROLL)
        if extra:
            sys.stderr.write("cme_public_projection: campo no permitido: %s\n" % extra[0])
            sys.exit(2)
        for key in ("start", "end", "imm"):
            if key in value and value[key] is not None and _date(value[key]) is None:
                sys.stderr.write("cme_public_projection: %s no es una fecha\n" % key)
                sys.exit(2)
        if "active" in value and value["active"] is not None and not isinstance(value["active"], bool):
            sys.stderr.write("cme_public_projection: active no es un booleano\n")
            sys.exit(2)
        return
    if where == "currency":
        if not isinstance(value, dict):
            sys.stderr.write("cme_public_projection: divisa no válida\n")
            sys.exit(2)
        extra = sorted(set(value) - ALLOWED_CURRENCY)
        if extra:
            sys.stderr.write("cme_public_projection: campo no permitido: %s\n" % extra[0])
            sys.exit(2)
        for key in ("date", "exp1"):
            if key in value and value[key] is not None and _date(value[key]) is None:
                sys.stderr.write("cme_public_projection: %s no es una fecha\n" % key)
                sys.exit(2)
        return
    sys.stderr.write("cme_public_projection: lista blanca no reconocida\n")
    sys.exit(2)


def project(doc, fxvol_calendar=False):
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
        out_row = {
            "status": status,
            "vote": _vote(row, gate),
            "date": day,
        }
        if fxvol_calendar:
            out_row["exp1"] = _date(row.get("exp1"))
        currencies[ccy] = out_row
    if top is None:
        top = "OK" if any_ok else "UNAVAILABLE"
    out = {"status": top, "as_of": day, "currencies": currencies}
    if fxvol_calendar:
        out["generated_utc"] = _timestamp(doc.get("generated_utc"))
        out["fut_roll_window"] = _roll_window(doc.get("fut_roll_window"))
    _reject_numbers(out)
    _reject_disallowed(out, "top")
    return out


def unavailable(doc):
    reason = "datos CME en repo privado"
    as_of = None
    if isinstance(doc, dict):
        if isinstance(doc.get("reason"), str) and doc.get("reason"):
            reason = doc["reason"]
        as_of = _date(doc.get("as_of"))
    if as_of is None:
        as_of = date.today().isoformat()
    out = {"status": "UNAVAILABLE", "reason": reason, "as_of": as_of}
    _reject_numbers(out)
    _reject_disallowed(out, "top")
    return out


def emit(doc, fxvol_calendar=False):
    if not isinstance(doc, dict) or not doc:
        return unavailable({})
    if doc.get("status") == "UNAVAILABLE" and "currencies" not in doc and "latest" not in doc:
        return unavailable(doc)
    return project(doc, fxvol_calendar=fxvol_calendar)


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
        fxvol_calendar = os.path.basename(dest) == "CME_FXVOL_public.json"
        text = json.dumps(emit(doc, fxvol_calendar=fxvol_calendar), ensure_ascii=False, indent=1) + "\n"
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

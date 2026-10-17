"""C1 — bandera CB_EVENT_48H en SHADOW (complemento #5).

Lee el calendario oficial data/manual/cb_meetings.csv y cuenta días hábiles
hasta la próxima decisión de cada banco, en el calendario de esa jurisdicción
(sources/calendars.csv). No escribe BOOK_RISK.json, no importa dashboard_alerts.py
y no cambia la §00 publicada.

La bandera de una divisa es verdadera cuando
0 <= days_to_next_cb_meeting <= 2.
La de un par de los 28 del contrato es verdadera si al menos una pata lo es.
both_legs_in_window conserva el choque doble (las dos patas a la vez).
"""
import csv
import json
import os
from datetime import date, timedelta

sys_path_ready = False


def _scripts():
    global sys_path_ready
    here = os.path.dirname(os.path.abspath(__file__))
    if not sys_path_ready:
        import sys
        if here not in sys.path:
            sys.path.insert(0, here)
        sys_path_ready = True
    return here


# Divisa G8, código del CSV de reuniones, calendario de días hábiles.
CCY = (
    ("USD", "US", "US"),
    ("EUR", "EA", "TARGET"),
    ("GBP", "GB", "GB"),
    ("JPY", "JP", "JP"),
    ("AUD", "AU", "AU"),
    ("NZD", "NZ", "NZ"),
    ("CAD", "CA", "CA"),
    ("CHF", "CH", "CH"),
)
CAL_OF = {ccy: cal for ccy, _country, cal in CCY}
COUNTRY_OF = {ccy: country for ccy, country, _cal in CCY}
WINDOW_LO = 0
WINDOW_HI = 2
SHADOW_NAME = "CB_EVENT_48H_shadow.json"


def days_to_next_cb_meeting(as_of, meetings, is_business_day):
    """Días hábiles desde as_of hasta la próxima decisión >= as_of.

    El mismo día de la reunión devuelve 0. Cada día hábil posterior suma 1.
    El día de la decisión cuenta aunque el calendario lo tenga como festivo:
    es la fecha oficial del anuncio. Sin reunión futura devuelve None.
    """
    upcoming = sorted(d for d in meetings if d >= as_of)
    if not upcoming:
        return None
    nxt = upcoming[0]
    if nxt == as_of:
        return 0
    n = 0
    d = as_of
    while d < nxt:
        d += timedelta(days=1)
        if d == nxt or is_business_day(d):
            n += 1
    return n


def in_window(days):
    return days is not None and WINDOW_LO <= days <= WINDOW_HI


def load_meetings(path):
    """{código de país: [date, ...]} desde cb_meetings.csv. Ignora comentarios."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        rows = csv.DictReader(line for line in fh if line.strip() and not line.startswith("#"))
        for row in rows:
            country = (row.get("country") or "").strip()
            raw = (row.get("decision_date") or "").strip()
            if not country or not raw:
                continue
            out.setdefault(country, []).append(date.fromisoformat(raw))
    return out


def load_pairs(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return list(doc["pairs"]["list"])


def _legs(pair):
    base, quote = pair.split("/")
    return base, quote


def evaluate(as_of, meetings_by_country, is_business_day, pairs):
    """is_business_day(calendar_name, day) → bool. pairs es la lista del contrato."""
    currencies = {}
    for ccy, country, cal in CCY:
        dates = meetings_by_country.get(country) or []
        nxt = min((d for d in dates if d >= as_of), default=None)
        days = days_to_next_cb_meeting(as_of, dates, lambda day, cal=cal: is_business_day(cal, day))
        currencies[ccy] = {
            "country": country,
            "calendar": cal,
            "next_meeting": None if nxt is None else nxt.isoformat(),
            "days_to_next_cb_meeting": days,
            "CB_EVENT_48H": in_window(days),
        }
    pair_rows = {}
    for pair in pairs:
        base, quote = _legs(pair)
        db = currencies[base]["days_to_next_cb_meeting"]
        dq = currencies[quote]["days_to_next_cb_meeting"]
        pair_rows[pair] = {
            "days_to_next_cb_meeting": {base: db, quote: dq},
            "CB_EVENT_48H": in_window(db) or in_window(dq),
            "both_legs_in_window": in_window(db) and in_window(dq),
        }
    return {
        "schema": "cb-event-48h-shadow-2",
        "mode": "SHADOW",
        "as_of": as_of.isoformat(),
        "window_bd": [WINDOW_LO, WINDOW_HI],
        "rule": "Divisa: 0 <= days_to_next_cb_meeting <= 2. Par: al menos una pata. both_legs_in_window: las dos.",
        "currencies": currencies,
        "pairs": pair_rows,
    }


def build(as_of, root):
    _scripts()
    from g8common import schedule as SC  # noqa: E402

    cals = SC.load_calendars(root)
    meetings = load_meetings(os.path.join(root, "data", "manual", "cb_meetings.csv"))
    pairs = load_pairs(os.path.join(root, "sources", "suite_contract.json"))
    return evaluate(as_of, meetings, lambda name, day: SC.is_bd(cals, name, day), pairs)


def write_summary(summary, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    root = os.path.dirname(_scripts())
    out = build(date.today(), root)
    dest = os.path.join(root, "data", SHADOW_NAME)
    write_summary(out, dest)
    print("[cb_event_shadow] wrote", dest, "as_of", out["as_of"])

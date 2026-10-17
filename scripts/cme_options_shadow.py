"""C1 — DTE y ΔOI de las opciones CME en SHADOW (complemento #1).

No modifica scripts/cme_options_collector.py, scripts/build_options_summary.py
ni data/OPTIONS_SURFACE.json. No importa dashboard_alerts.py: la §08 publicada
sigue igual, inversión JPY/CAD/CHF incluida.

CALL WALL: strike estrictamente por encima de ref con más open interest de calls,
entre strikes con OI total >= 100 (la misma cota que el resumen publicado).
PUT WALL: strike estrictamente por debajo de ref con más open interest de puts.
Empate: gana el strike menor.

ΔOI es el cambio de ese OI del lado (y del agregado del vencimiento vigente)
frente a la sesión anterior. Sin snapshot previo, o sin ese strike en la sesión
anterior, ΔOI es None.
"""
import json
import os
from datetime import date, timedelta

MIN_OI = 100
CME_CALENDAR = "US"
CCY = ("EUR", "JPY", "GBP", "AUD", "CAD", "CHF")
SHADOW_NAME = "CME_OPTIONS_C1_shadow.json"


def dte(session, front, is_business_day):
    """dte_calendar es la resta de fechas. dte_bd cuenta días hábiles CME (calendario US)
    estrictamente posteriores a la sesión y hasta el vencimiento inclusive."""
    if not session or not front:
        return None, None
    start = date.fromisoformat(session) if isinstance(session, str) else session
    expiry = date.fromisoformat(front) if isinstance(front, str) else front
    calendar = (expiry - start).days
    if expiry <= start:
        return calendar, 0
    n = 0
    d = start
    while d < expiry:
        d += timedelta(days=1)
        if is_business_day(d):
            n += 1
    return calendar, n


def _strike_key(k):
    return round(float(k), 8)


def walls(chain, ref, min_oi=MIN_OI):
    """chain: {strike: {'c': oi_call, 'p': oi_put}}. ref es el futuro de referencia."""
    if ref is None or not chain:
        return None, None
    kept = {}
    for k, e in chain.items():
        c = float(e.get("c") or 0.0)
        p = float(e.get("p") or 0.0)
        if c + p >= min_oi:
            kept[_strike_key(k)] = {"c": c, "p": p}
    above = {k: e for k, e in kept.items() if k > ref}
    below = {k: e for k, e in kept.items() if k < ref}
    call = min(above.items(), key=lambda kv: (-kv[1]["c"], kv[0])) if above else None
    put = min(below.items(), key=lambda kv: (-kv[1]["p"], kv[0])) if below else None
    return call, put


def _lookup(chain, strike):
    if not chain or strike is None:
        return None
    return chain.get(_strike_key(strike))


def _delta(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def _wall_row(side, picked, prev_chain):
    if picked is None:
        return None
    strike, e = picked
    oi = e["c"] if side == "c" else e["p"]
    prev = _lookup(prev_chain, strike)
    prev_oi = None if prev is None else (prev["c"] if side == "c" else prev["p"])
    return {
        "k": strike,
        "oi": oi,
        "delta_oi": _delta(oi, prev_oi),
    }


def enrich(session, front, ref, oi_front, chain, previous, is_business_day):
    """previous es None o un dict con front, oi_front y chain. ΔOI queda en None sin previo."""
    calendar, business = dte(session, front, is_business_day)
    prev_chain = None if previous is None else previous.get("chain")
    call, put = walls(chain, ref)
    agg_prev = None if previous is None else previous.get("oi_front")
    return {
        "front_expiry": front,
        "ref": ref,
        "dte_calendar": calendar,
        "dte_bd": business,
        "call_wall": _wall_row("c", call, prev_chain),
        "put_wall": _wall_row("p", put, prev_chain),
        "aggregate": {
            "oi": oi_front,
            "delta_oi": _delta(oi_front, agg_prev),
        },
    }


def chain_from_walls(rows):
    out = {}
    for row in rows or []:
        out[_strike_key(row["k"])] = {"c": float(row.get("c") or 0.0), "p": float(row.get("p") or 0.0)}
    return out


def _ccy_block(session, block, prev, is_business_day):
    if not block:
        return {
            "front_expiry": None,
            "ref": None,
            "dte_calendar": None,
            "dte_bd": None,
            "call_wall": None,
            "put_wall": None,
            "aggregate": {"oi": None, "delta_oi": None},
        }
    previous = None
    if prev:
        previous = {
            "front": prev.get("front"),
            "oi_front": prev.get("oi_front"),
            "chain": chain_from_walls(prev.get("walls")),
        }
    return enrich(
        session,
        block.get("front"),
        block.get("ref"),
        block.get("oi_front"),
        chain_from_walls(block.get("walls")),
        previous,
        is_business_day,
    )


def from_surface(doc, is_business_day):
    """Enriquece el JSON publicado. Los muros salen de los strikes que ese JSON guarda
    (los cinco de mayor OI total). No reescribe el archivo."""
    session = doc.get("latest_session")
    latest = doc.get("latest") or {}
    prev = doc.get("prev") or {}
    currencies = {}
    for ccy in CCY:
        currencies[ccy] = _ccy_block(session, latest.get(ccy), prev.get(ccy), is_business_day)
    currencies["NZD"] = {
        "front_expiry": None,
        "ref": None,
        "dte_calendar": None,
        "dte_bd": None,
        "call_wall": None,
        "put_wall": None,
        "aggregate": {"oi": None, "delta_oi": None},
        "note": "sin opciones CME",
    }
    return {
        "schema": "cme-options-c1-shadow-1",
        "mode": "SHADOW",
        "session": session,
        "prev_session_present": bool(prev),
        "chain": "OPTIONS_SURFACE.json walls (top 5 by total OI). Canonical CSVs are not in the repo.",
        "convention": "CME (XXX/USD). La inversión de JPY/CAD/CHF de la §08 sigue solo en dashboard_alerts.py.",
        "min_oi": MIN_OI,
        "dte_calendar_name": CME_CALENDAR,
        "currencies": currencies,
    }


def build(root):
    import sys
    scripts = os.path.dirname(os.path.abspath(__file__))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from g8common import schedule as SC

    cals = SC.load_calendars(root)
    with open(os.path.join(root, "data", "OPTIONS_SURFACE.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    return from_surface(doc, lambda day: SC.is_bd(cals, CME_CALENDAR, day))


def write_summary(summary, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = build(root)
    dest = os.path.join(root, "data", SHADOW_NAME)
    write_summary(out, dest)
    print("[cme_options_shadow] wrote", dest, "session", out["session"])

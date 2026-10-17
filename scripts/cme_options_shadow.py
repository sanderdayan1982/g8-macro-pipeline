"""C2 — distancia, muro cercano, biografía y roll de las opciones CME en SHADOW.

Extiende el complemento C1. No modifica scripts/cme_options_collector.py,
scripts/build_options_summary.py ni data/OPTIONS_SURFACE.json. No importa
dashboard_alerts.py (§00) y docs/index.html (§06) invierten JPY/CAD/CHF una sola vez. Este JSON se queda nativo.

CALL WALL: strike estrictamente por encima de ref con más open interest de calls,
entre strikes con OI total >= 100. PUT WALL: strike estrictamente por debajo de ref
con más open interest de puts. Empate: gana el strike menor. Esos dos son los muros
absolutos y no se sustituyen.

NEAREST CALL / NEAREST PUT: entre los strikes que pasan MIN_OI, el call con c > 0
más cercano por encima de ref, y el put con p > 0 más cercano por debajo. Empate
de distancia: gana el strike menor.

ΔOI de 1 día compara con la sesión previa del JSON publicado. 1 semana son 5
sesiones atrás y 1 mes son 21, leídas de data/options/shadow_history.json, el mismo
(vencimiento, strike). Si esa sesión o ese strike no están, el delta es None.
"""
import json
import os
from datetime import date, timedelta

MIN_OI = 100
NEAR_PIN_PCT = 0.5
CHIP_PCT = 15.0
DRAIN_1_3 = -33.33
DRAIN_2_3 = -66.67
ROLL_DTE = 7
LOOKBACK_1W = 5
LOOKBACK_1M = 21
HISTORY_KEEP = 30
CME_CALENDAR = "US"
CCY = ("EUR", "JPY", "GBP", "AUD", "CAD", "CHF")
SHADOW_NAME = "CME_OPTIONS_C1_shadow.json"
HISTORY_NAME = os.path.join("options", "shadow_history.json")
ROLL_NOTE = "EXPIRING_≤7D_USE_NEXT_MONTH"


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


def _kept(chain, min_oi):
    kept = {}
    for k, e in (chain or {}).items():
        c = float(e.get("c") or 0.0)
        p = float(e.get("p") or 0.0)
        if c + p >= min_oi:
            kept[_strike_key(k)] = {"c": c, "p": p}
    return kept


def walls(chain, ref, min_oi=MIN_OI):
    """chain: {strike: {'c': oi_call, 'p': oi_put}}. ref es el futuro de referencia."""
    if ref is None or not chain:
        return None, None
    kept = _kept(chain, min_oi)
    above = {k: e for k, e in kept.items() if k > ref}
    below = {k: e for k, e in kept.items() if k < ref}
    call = min(above.items(), key=lambda kv: (-kv[1]["c"], kv[0])) if above else None
    put = min(below.items(), key=lambda kv: (-kv[1]["p"], kv[0])) if below else None
    return call, put


def nearest_walls(chain, ref, min_oi=MIN_OI):
    """Muro táctico: menor distancia a ref, no el mayor OI."""
    if ref is None or not chain:
        return None, None
    kept = _kept(chain, min_oi)
    above = [(k, e) for k, e in kept.items() if k > ref and e["c"] > 0]
    below = [(k, e) for k, e in kept.items() if k < ref and e["p"] > 0]
    call = min(above, key=lambda kv: (kv[0] - ref, kv[0])) if above else None
    put = min(below, key=lambda kv: (ref - kv[0], kv[0])) if below else None
    return call, put


def _lookup(chain, strike):
    if not chain or strike is None:
        return None
    return chain.get(_strike_key(strike))


def _delta(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def _pct(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100.0, 2)


def _chip15(pct):
    if pct is None:
        return None
    return abs(pct) >= CHIP_PCT


def _drain(pct, cutoff):
    if pct is None:
        return None
    return pct <= cutoff


def _dist(strike, ref):
    if strike is None or ref in (None, 0):
        return None, None, None
    raw = (strike - ref) / ref * 100.0
    return round(raw, 2), round(abs(raw), 2), abs(raw) <= NEAR_PIN_PCT


def _normalize_chain(chain):
    if not chain:
        return {}
    out = {}
    for k, e in chain.items():
        out[_strike_key(k)] = {"c": float(e.get("c") or 0.0), "p": float(e.get("p") or 0.0)}
    return out


def _prior_session(history, session, n):
    """La sesión n plazas atrás, sin contar la sesión actual."""
    if not history or not session or n < 1:
        return None
    rows = [s for s in history.get("sessions") or [] if s.get("session") and s["session"] < session]
    rows.sort(key=lambda s: s["session"])
    if len(rows) < n:
        return None
    return rows[-n]


def _past_side(history, session, ccy, front, strike, side, n):
    snap = _prior_session(history, session, n)
    if snap is None or not ccy:
        return None
    block = (snap.get("currencies") or {}).get(ccy) or {}
    if front and block.get("front") != front:
        return None
    row = _lookup(_normalize_chain(block.get("chain")), strike)
    if row is None:
        return None
    return row[side]


def _past_aggregate(history, session, ccy, front, n):
    snap = _prior_session(history, session, n)
    if snap is None or not ccy:
        return None
    block = (snap.get("currencies") or {}).get(ccy) or {}
    if front and block.get("front") != front:
        return None
    oi = block.get("oi_front")
    return None if oi is None else float(oi)


def _wall_row(side, picked, ref, prev_chain, history, session, ccy, front):
    if picked is None:
        return None
    strike, e = picked
    oi = e["c"] if side == "c" else e["p"]
    prev = _lookup(prev_chain, strike)
    prev_oi = None if prev is None else (prev["c"] if side == "c" else prev["p"])
    pct = _pct(oi, prev_oi)
    dist, abs_dist, near = _dist(strike, ref)
    week = _past_side(history, session, ccy, front, strike, side, LOOKBACK_1W)
    month = _past_side(history, session, ccy, front, strike, side, LOOKBACK_1M)
    return {
        "k": strike,
        "oi": oi,
        "delta_oi": _delta(oi, prev_oi),
        "delta_oi_1d": _delta(oi, prev_oi),
        "pct_oi_1d": pct,
        "delta_oi_1w": _delta(oi, week),
        "delta_oi_1m": _delta(oi, month),
        "chip_15pct_strike": _chip15(pct),
        "dist_pct": dist,
        "abs_dist_pct": abs_dist,
        "near_pin_05pct": near,
    }


def _aggregate(oi_front, prev_oi, history, session, ccy, front):
    pct = _pct(oi_front, prev_oi)
    week = _past_aggregate(history, session, ccy, front, LOOKBACK_1W)
    month = _past_aggregate(history, session, ccy, front, LOOKBACK_1M)
    return {
        "oi": oi_front,
        "delta_oi": _delta(oi_front, prev_oi),
        "delta_oi_1d": _delta(oi_front, prev_oi),
        "pct_oi_1d": pct,
        "delta_oi_1w": _delta(oi_front, week),
        "delta_oi_1m": _delta(oi_front, month),
        "chip_15pct_chain": _chip15(pct),
        "drain_1_3": _drain(pct, DRAIN_1_3),
        "drain_2_3": _drain(pct, DRAIN_2_3),
    }


def _empty_aggregate():
    return _aggregate(None, None, None, None, None, None)


def map_status(ccy, calendar, call_wall, put_wall, roll):
    if ccy == "NZD":
        return "NO_CME_OPTIONS"
    if roll:
        return "EXPIRING_≤7D"
    if call_wall is None or put_wall is None:
        return "THIN_ONE_SIDED"
    if calendar is not None and calendar > ROLL_DTE:
        return "READABLE"
    return "THIN_ONE_SIDED"


def enrich(session, front, ref, oi_front, chain, previous, is_business_day, history=None, ccy=None):
    """previous es None o un dict con front, oi_front y chain. ΔOI queda en None sin previo."""
    calendar, business = dte(session, front, is_business_day)
    prev_chain = None if previous is None else _normalize_chain(previous.get("chain"))
    call, put = walls(chain, ref)
    near_call, near_put = nearest_walls(chain, ref)
    agg_prev = None if previous is None else previous.get("oi_front")
    call_row = _wall_row("c", call, ref, prev_chain, history, session, ccy, front)
    put_row = _wall_row("p", put, ref, prev_chain, history, session, ccy, front)
    roll = calendar is not None and calendar <= ROLL_DTE
    row = {
        "front_expiry": front,
        "ref": ref,
        "dte_calendar": calendar,
        "dte_bd": business,
        "roll_window_7d": roll,
        "map_status": map_status(ccy, calendar, call_row, put_row, roll),
        "call_wall": call_row,
        "put_wall": put_row,
        "nearest_call_wall": _wall_row("c", near_call, ref, prev_chain, history, session, ccy, front),
        "nearest_put_wall": _wall_row("p", near_put, ref, prev_chain, history, session, ccy, front),
        "aggregate": _aggregate(oi_front, agg_prev, history, session, ccy, front),
    }
    if roll:
        row["note"] = ROLL_NOTE
    return row


def chain_from_walls(rows):
    out = {}
    for row in rows or []:
        out[_strike_key(row["k"])] = {"c": float(row.get("c") or 0.0), "p": float(row.get("p") or 0.0)}
    return out


def _ccy_block(session, block, prev, is_business_day, history, ccy):
    if not block:
        row = {
            "front_expiry": None,
            "ref": None,
            "dte_calendar": None,
            "dte_bd": None,
            "roll_window_7d": False,
            "map_status": map_status(ccy, None, None, None, False),
            "call_wall": None,
            "put_wall": None,
            "nearest_call_wall": None,
            "nearest_put_wall": None,
            "aggregate": _empty_aggregate(),
        }
        return row
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
        history,
        ccy,
    )


def _nzd():
    return {
        "front_expiry": None,
        "ref": None,
        "dte_calendar": None,
        "dte_bd": None,
        "roll_window_7d": False,
        "map_status": "NO_CME_OPTIONS",
        "call_wall": None,
        "put_wall": None,
        "nearest_call_wall": None,
        "nearest_put_wall": None,
        "aggregate": _empty_aggregate(),
        "note": "sin opciones CME",
    }


def from_surface(doc, is_business_day, history=None):
    """Enriquece el JSON publicado. Los muros salen de los strikes que ese JSON guarda
    (los cinco de mayor OI total). No reescribe el archivo."""
    session = doc.get("latest_session")
    latest = doc.get("latest") or {}
    prev = doc.get("prev") or {}
    currencies = {}
    for ccy in CCY:
        currencies[ccy] = _ccy_block(session, latest.get(ccy), prev.get(ccy), is_business_day, history, ccy)
    currencies["NZD"] = _nzd()
    return {
        "schema": "cme-options-c2-shadow-1",
        "mode": "SHADOW",
        "session": session,
        "prev_session_present": bool(prev),
        "chain": "OPTIONS_SURFACE.json walls (top 5 by total OI). Canonical CSVs are not in the repo.",
        "convention": "CME (XXX/USD). En este JSON los niveles y dist_pct son nativos; tanto dashboard_alerts.py (§00) como docs/index.html (§06) invierten una sola vez (1/k y cruce call/put) para JPY, CAD y CHF. No volver a invertir.",
        "min_oi": MIN_OI,
        "near_pin_pct": NEAR_PIN_PCT,
        "chip_pct": CHIP_PCT,
        "drain_1_3_pct": DRAIN_1_3,
        "drain_2_3_pct": DRAIN_2_3,
        "roll_dte": ROLL_DTE,
        "dte_calendar_name": CME_CALENDAR,
        "currencies": currencies,
    }


def empty_history():
    return {"schema": "cme-options-shadow-history-1", "sessions": []}


def load_history(root):
    path = os.path.join(root, "data", HISTORY_NAME)
    if not os.path.exists(path):
        return empty_history()
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get("sessions"), list):
        return empty_history()
    return doc


def snapshot_from_doc(doc):
    session = doc.get("latest_session")
    currencies = {}
    for ccy in CCY:
        block = (doc.get("latest") or {}).get(ccy)
        if not block:
            continue
        currencies[ccy] = {
            "front": block.get("front"),
            "oi_front": block.get("oi_front"),
            "chain": chain_from_walls(block.get("walls")),
        }
    return {"session": session, "currencies": currencies}


def remember(history, snap, keep=HISTORY_KEEP):
    """Historial rotativo. La sesión actual reemplaza una copia previa de la misma fecha."""
    sessions = [s for s in (history or empty_history()).get("sessions") or [] if s.get("session") != snap.get("session")]
    if snap.get("session"):
        sessions.append(snap)
    sessions.sort(key=lambda s: s["session"])
    return {"schema": "cme-options-shadow-history-1", "sessions": sessions[-keep:]}


def build(root):
    import sys
    scripts = os.path.dirname(os.path.abspath(__file__))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from g8common import schedule as SC

    cals = SC.load_calendars(root)
    with open(os.path.join(root, "data", "OPTIONS_SURFACE.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    return from_surface(doc, lambda day: SC.is_bd(cals, CME_CALENDAR, day), load_history(root))


def write_summary(summary, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _jsonable_chain(chain):
    return {str(k): v for k, v in chain.items()}


def _jsonable_snap(snap):
    currencies = {}
    for ccy, block in (snap.get("currencies") or {}).items():
        currencies[ccy] = {
            "front": block.get("front"),
            "oi_front": block.get("oi_front"),
            "chain": _jsonable_chain(block.get("chain") or {}),
        }
    return {"session": snap.get("session"), "currencies": currencies}


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "data", "OPTIONS_SURFACE.json"), encoding="utf-8") as fh:
        surface = json.load(fh)
    history = remember(load_history(root), _jsonable_snap(snapshot_from_doc(surface)))
    write_summary(history, os.path.join(root, "data", HISTORY_NAME))
    out = build(root)
    dest = os.path.join(root, "data", SHADOW_NAME)
    write_summary(out, dest)
    print("[cme_options_shadow] wrote", dest, "session", out["session"])

#!/usr/bin/env python3
"""FAMILIAS-01 C — estados FXVOL (SHADOW).

Función pura de la historia (la misma rejilla que `data/options/fxvol_history.csv`).
No guarda estado: cada pasada recorre la serie entera, así que una sesión nueva
no reescribe las anteriores.

VOTE = 0. No entra en `blocked_entry`. No lee la red. Los cortes son constantes
(tabla firmada); no hay ajuste.

Plan B: IV = iv30_atm (una sola serie por ventana). SK = rr25 con el signo R11
aplicado una sola vez (JPY, CAD, CHF). RV21 = settle del futuro CME del mismo
contrato, la regla de 21 rendimientos ya usada en el shadow.

La bandera que decide si esto se escribe vive en `cme_fxvol_shadow.py`
(`FXVOL_STATES_PUBLISH`). Este módulo no escribe ficheros.
"""
import csv
import io
import math
from datetime import date, timedelta

# Cortes firmados. No se calibran aquí.
HIST_MIN = 60
HIST_FULL = 252
LB_5 = 5
RV_RETURNS = 21
HYSTERESIS_CLOSES = 2
P_ABS_DSK = 90

VOL_QUIET_ENTER = 20
VOL_QUIET_EXIT = 30
VOL_BID_ENTER = 80
VOL_BID_EXIT = 70
VOL_STRESS_ENTER = 95
VOL_STRESS_EXIT = 85
VRP_EVENT_ENTER = 90
VRP_EVENT_EXIT = 80
SK_Z_ENTER = 1.5
SK_Z_EXIT = 1.0

DOT = "·"
R11_FLIP = frozenset(("JPY", "CAD", "CHF"))
PROV_KEYS = ("vol", "premium", "skew", "turn")

# Cabecera method.states. El [PROV] de umbrales sin calibrar se queda
# hasta un acta de GATE. Solo se copia al JSON si la bandera de publicación
# está encendida.
METHOD_STATES = {
    "shadow": True,
    "vote": 0,
    "blocked_entry": False,
    "header": "[PROV] umbrales sin calibrar hasta acta de GATE",
    "hysteresis_closes": HYSTERESIS_CLOSES,
    "hist_min": HIST_MIN,
    "window": HIST_FULL,
    "lookback_sessions": LB_5,
    "rv_returns": RV_RETURNS,
    "r11_flip_once": ["JPY", "CAD", "CHF"],
    "vol_precedence": ["STRESS", "BID", "QUIET", "NORMAL"],
    "stress_exit": "BID si IV_pct>=70; si no NORMAL",
    "empty": DOT,
    "stale_business_days": 1,
    "percentile": "rango empirico, incluye el valor de hoy, redondeo a 1 decimal, como iv_pct",
    "p90": "interpolacion lineal, la de dashboard_alerts.pct",
    "z_stdev": "muestral N-1",
    "thresholds": {
        "QUIET_enter_le": VOL_QUIET_ENTER,
        "QUIET_exit_gt": VOL_QUIET_EXIT,
        "BID_enter_ge": VOL_BID_ENTER,
        "BID_exit_lt": VOL_BID_EXIT,
        "STRESS_enter_ge": VOL_STRESS_ENTER,
        "STRESS_exit_lt": VOL_STRESS_EXIT,
        "STRESS_delta_gt": 0,
        "EVENT_enter_ge": VRP_EVENT_ENTER,
        "EVENT_exit_lt": VRP_EVENT_EXIT,
        "CALL_enter_ge": SK_Z_ENTER,
        "CALL_exit_lt": SK_Z_EXIT,
        "PUT_enter_le": -SK_Z_ENTER,
        "PUT_exit_gt": -SK_Z_EXIT,
        "abs_dsk_p": P_ABS_DSK,
    },
}

PUBLISHED_FIELDS = (
    "vol_state",
    "premium_state",
    "skew_state",
    "skew_turn",
    "sk_z",
    "vrp_pct",
    "states_prov",
    "states_reason",
)


def r11_level(ccy, rr25):
    """Signo R11, una sola vez. JPY/CAD/CHF cambian de signo; el resto no."""
    if rr25 is None:
        return None
    if ccy in R11_FLIP:
        return -rr25
    return rr25


def signo(x):
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _num(value):
    """Número presente. Vacío no es 0."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return None
        return number
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.upper() in ("NA", "NAN", "NONE"):
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _valid(row):
    if (row.get("status") or "OK") != "OK":
        return False
    return all(_num(row.get(key)) is not None for key in ("iv30_atm", "rr25", "bf25"))


def _blank_prov():
    return {key: False for key in PROV_KEYS}


def _reason(proxy, provisional, parts):
    bits = []
    if proxy:
        bits.append("[PROXY]")
    if provisional:
        bits.append("[PROV]")
    bits.extend(part for part in parts if part)
    return " ".join(bits)


def pct_rank(values, x):
    """Percentil empírico (mismo criterio que iv_pct): % de valores ≤ x, 1 decimal."""
    n = len(values)
    if n < HIST_MIN:
        return None
    return round(100.0 * sum(1 for value in values if value <= x) / n, 1)


def quantile_linear(values, q):
    """Percentil q por interpolación lineal. Misma cuenta que dashboard_alerts.pct."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    k = (n - 1) * q / 100.0
    lo = int(math.floor(k))
    hi = min(lo + 1, n - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def zscore(values):
    """Z del último valor. Desviación muestral (N−1). None si no hay dispersión."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((value - mean) ** 2 for value in values) / (n - 1)
    if var <= 0.0:
        return None
    return (values[-1] - mean) / math.sqrt(var)


def desired_vol(state, pct, d1w):
    """Estado que pediría la tabla, dada la posición ya confirmada.

    Precedencia STRESS > BID > QUIET > NORMAL. La salida de STRESS es BID si
    el percentil sigue ≥ 70 y NORMAL en caso contrario (firmado): no salta a QUIET.
    """
    stress_enter = pct >= VOL_STRESS_ENTER and d1w > 0
    bid_enter = pct >= VOL_BID_ENTER
    quiet_enter = pct <= VOL_QUIET_ENTER
    if state == "STRESS":
        if pct < VOL_STRESS_EXIT:
            if pct >= VOL_BID_EXIT:
                return "BID"
            return "NORMAL"
        return "STRESS"
    if state == "BID":
        if stress_enter:
            return "STRESS"
        if pct < VOL_BID_EXIT:
            if quiet_enter:
                return "QUIET"
            return "NORMAL"
        return "BID"
    if state == "QUIET":
        if stress_enter:
            return "STRESS"
        if bid_enter:
            return "BID"
        if pct > VOL_QUIET_EXIT:
            return "NORMAL"
        return "QUIET"
    if stress_enter:
        return "STRESS"
    if bid_enter:
        return "BID"
    if quiet_enter:
        return "QUIET"
    return "NORMAL"


def desired_premium(state, pct):
    if state == "EVENT":
        if pct < VRP_EVENT_EXIT:
            return DOT
        return "EVENT"
    if pct >= VRP_EVENT_ENTER:
        return "EVENT"
    return DOT


def desired_skew(state, z):
    call_enter = z >= SK_Z_ENTER
    put_enter = z <= -SK_Z_ENTER
    if state == "CALL-BID":
        if z >= SK_Z_EXIT:
            return "CALL-BID"
        if put_enter:
            return "PUT-BID"
        return DOT
    if state == "PUT-BID":
        if z <= -SK_Z_EXIT:
            return "PUT-BID"
        if call_enter:
            return "CALL-BID"
        return DOT
    if call_enter:
        return "CALL-BID"
    if put_enter:
        return "PUT-BID"
    return DOT


def step_hysteresis(state, arm, count, desired, closes=HYSTERESIS_CLOSES):
    """Dos cierres consecutivos para entrar y para salir. Devuelve publicado, brazo, cuenta."""
    if desired == state:
        return state, None, 0
    if desired == arm:
        count += 1
    else:
        arm = desired
        count = 1
    if count >= closes:
        return desired, None, 0
    return state, arm, count


def turn_label(dsk, abs_window, sk, sk_lag):
    """Evento del día, sin histéresis. abs_window incluye el |dSK| de hoy."""
    if dsk is None or sk is None or sk_lag is None:
        return "UNAVAILABLE", False, "falta dSK"
    n = len(abs_window)
    if n < HIST_MIN:
        return "UNAVAILABLE", False, "n<60"
    window = abs_window[-HIST_FULL:]
    threshold = quantile_linear(window, P_ABS_DSK)
    fired = abs(dsk) >= threshold and signo(sk) != signo(sk_lag)
    return ("SK-TURN" if fired else DOT), len(window) < HIST_FULL, ""


def business_days_after(session, asof):
    """Días hábiles L–V estrictamente posteriores a la sesión, hasta asof incluido."""
    if isinstance(session, str):
        session = date.fromisoformat(session[:10])
    if isinstance(asof, str):
        asof = date.fromisoformat(asof[:10])
    if asof <= session:
        return 0
    count = 0
    cursor = session + timedelta(days=1)
    while cursor <= asof:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def _roll_indices(rows):
    rolls = set()
    prev = None
    for index, row in enumerate(rows):
        contract = row.get("px_contract") or ""
        if contract and prev and contract != prev:
            rolls.add(index)
        if contract:
            prev = contract
    return rolls


def rv21_at(rows, index, rolls):
    """RV21 en % (puntos de vol). 21 rendimientos log del mismo contrato, σ muestral, √252.

    El día en que cambia el contrato no entra. Si no hay 21 rendimientos, None.
    """
    symbol = rows[index].get("px_contract") or ""
    if not symbol:
        return None
    prices = []
    for cursor in range(index, -1, -1):
        if cursor in rolls:
            break
        if (rows[cursor].get("px_contract") or "") != symbol:
            break
        price = _num(rows[cursor].get("px_settle"))
        if price is None or price <= 0:
            break
        prices.append(price)
        if len(prices) == RV_RETURNS + 1:
            break
    prices.reverse()
    if len(prices) - 1 < RV_RETURNS:
        return None
    returns = [
        math.log(prices[cursor] / prices[cursor - 1])
        for cursor in range(len(prices) - RV_RETURNS, len(prices))
    ]
    mean = sum(returns) / float(RV_RETURNS)
    var = sum((value - mean) ** 2 for value in returns) / float(RV_RETURNS - 1)
    return round(math.sqrt(var) * math.sqrt(252.0) * 100.0, 2)


def _nzd_row(session):
    return {
        "session": session,
        "vol_state": "NA",
        "premium_state": "NA",
        "skew_state": "NA",
        "skew_turn": "NA",
        "sk_z": "NA",
        "vrp_pct": "NA",
        "states_prov": _blank_prov(),
        "states_reason": "NO_CME_OPTIONS",
    }


def _row(session, vol, premium, skew, turn, sk_z, vrp_pct, prov, reason):
    return {
        "session": session,
        "vol_state": vol,
        "premium_state": premium,
        "skew_state": skew,
        "skew_turn": turn,
        "sk_z": sk_z,
        "vrp_pct": vrp_pct,
        "states_prov": prov,
        "states_reason": reason,
    }


def _unavailable_row(session, proxy, detail):
    return _row(
        session,
        "UNAVAILABLE",
        "UNAVAILABLE",
        "UNAVAILABLE",
        "UNAVAILABLE",
        "UNAVAILABLE",
        "UNAVAILABLE",
        _blank_prov(),
        _reason(proxy, False, [detail]),
    )


def _missing_detail(row):
    missing = []
    if _num(row.get("iv30_atm")) is None:
        missing.append("falta IV")
    if _num(row.get("rr25")) is None:
        missing.append("falta RR")
    if _num(row.get("bf25")) is None:
        missing.append("falta BF")
    if not missing and (row.get("status") or "OK") != "OK":
        missing.append(row.get("reason") or "sesion no OK")
    return "; ".join(missing) if missing else "sesion ausente"


def _apply_stale(series, asof, proxy):
    if asof is None or not series:
        return series
    last = series[-1]
    session = last.get("session")
    if not session:
        return series
    if business_days_after(session, asof) <= 1:
        return series
    covered = dict(last)
    covered["vol_state"] = "STALE"
    covered["premium_state"] = "STALE"
    covered["skew_state"] = "STALE"
    covered["skew_turn"] = "STALE"
    covered["states_prov"] = _blank_prov()
    covered["states_reason"] = _reason(proxy, False, ["STALE"])
    series[-1] = covered
    return series


def series_for_currency(ccy, rows, asof=None, gate0=None):
    """Estados causales de una divisa. `rows` en orden de sesión, sin huecos rellenos."""
    if ccy == "NZD":
        if not rows:
            session = asof.isoformat() if isinstance(asof, date) else asof
            return [_nzd_row(session)]
        return [_nzd_row(row.get("session")) for row in rows]

    proxy = gate0 == "PROXY"
    rolls = _roll_indices(rows)
    iv_hist = []
    sk_hist = []
    vrp_hist = []
    abs_dsk = []
    vol_state, vol_arm, vol_count = "NORMAL", None, 0
    prem_state, prem_arm, prem_count = DOT, None, 0
    skew_state, skew_arm, skew_count = DOT, None, 0
    out = []

    for index, row in enumerate(rows):
        session = row.get("session")
        if not _valid(row):
            out.append(_unavailable_row(session, proxy, _missing_detail(row)))
            vol_state, vol_arm, vol_count = "NORMAL", None, 0
            prem_state, prem_arm, prem_count = DOT, None, 0
            skew_state, skew_arm, skew_count = DOT, None, 0
            continue

        iv = _num(row.get("iv30_atm"))
        rr = _num(row.get("rr25"))
        sk = r11_level(ccy, rr)
        iv_hist.append(iv)
        sk_hist.append(sk)

        d1w = None
        dsk = None
        sk_lag = None
        if index >= LB_5 and _valid(rows[index - LB_5]):
            lag_iv = _num(rows[index - LB_5].get("iv30_atm"))
            lag_rr = _num(rows[index - LB_5].get("rr25"))
            sk_lag = r11_level(ccy, lag_rr)
            d1w = round(iv - lag_iv, 2)
            dsk = round(sk - sk_lag, 2)
            abs_dsk.append(abs(dsk))

        iv_window = iv_hist[-HIST_FULL:]
        n_iv = len(iv_window)
        iv_pct = pct_rank(iv_window, iv)
        vol_prov = iv_pct is not None and n_iv < HIST_FULL

        sk_window = sk_hist[-HIST_FULL:]
        n_sk = len(sk_window)
        z = zscore(sk_window) if n_sk >= HIST_MIN else None
        skew_prov = z is not None and n_sk < HIST_FULL

        rv = rv21_at(rows, index, rolls)
        vrp = None if rv is None else round(round(iv, 2) - rv, 2)
        if vrp is not None:
            vrp_hist.append(vrp)
        # VRP_pct window (HIST_FULL): the rule is 252 VRP observations
        # (≈ 273 sessions with the current RV21 rule of 22 settles).
        vrp_window = vrp_hist[-HIST_FULL:]
        n_vrp = len(vrp_window) if vrp is not None else 0
        vrp_pct = pct_rank(vrp_window, vrp) if vrp is not None else None
        prem_prov = vrp_pct is not None and n_vrp < HIST_FULL

        reasons = []
        prov = _blank_prov()

        if iv_pct is None or d1w is None:
            published_vol = "UNAVAILABLE"
            vol_state, vol_arm, vol_count = "NORMAL", None, 0
            reasons.append("n<60" if n_iv < HIST_MIN else "falta dIV1w")
        else:
            desired = desired_vol(vol_state, iv_pct, d1w)
            published_vol, vol_arm, vol_count = step_hysteresis(vol_state, vol_arm, vol_count, desired)
            vol_state = published_vol
            prov["vol"] = vol_prov

        if vrp is None or vrp_pct is None:
            published_prem = "UNAVAILABLE"
            prem_state, prem_arm, prem_count = DOT, None, 0
            reasons.append("falta RV21" if vrp is None else "n<60")
        else:
            desired = desired_premium(prem_state, vrp_pct)
            published_prem, prem_arm, prem_count = step_hysteresis(prem_state, prem_arm, prem_count, desired)
            prem_state = published_prem
            prov["premium"] = prem_prov

        if z is None:
            published_skew = "UNAVAILABLE"
            skew_state, skew_arm, skew_count = DOT, None, 0
            reasons.append("n<60" if n_sk < HIST_MIN else "desv 0")
        else:
            desired = desired_skew(skew_state, z)
            published_skew, skew_arm, skew_count = step_hysteresis(skew_state, skew_arm, skew_count, desired)
            skew_state = published_skew
            prov["skew"] = skew_prov

        published_turn, turn_prov, turn_why = turn_label(dsk, abs_dsk, sk, sk_lag)
        if published_turn == "UNAVAILABLE":
            reasons.append(turn_why)
        else:
            prov["turn"] = turn_prov

        sk_out = "UNAVAILABLE" if z is None else round(z, 6)
        vrp_out = "UNAVAILABLE" if vrp_pct is None else vrp_pct
        # Una columna en UNAVAILABLE no arrastra el número de esa columna.
        if published_skew == "UNAVAILABLE":
            sk_out = "UNAVAILABLE"
        if published_prem == "UNAVAILABLE":
            vrp_out = "UNAVAILABLE"
        out.append(_row(
            session,
            published_vol,
            published_prem,
            published_skew,
            published_turn,
            sk_out,
            vrp_out,
            prov,
            _reason(proxy, any(prov.values()), reasons),
        ))

    return _apply_stale(out, asof, proxy)


def compute_fxvol_states(rows_by_ccy, asof=None, gate0=None):
    """Por divisa, la serie de estados. NZD sale NA aunque no venga en la entrada.

    `gate0` es el veredicto ya resuelto (CLEAN, PROXY, …). PROXY etiqueta
    `[PROXY]` y se clasifica igual.
    """
    gate0 = gate0 or {}
    result = {}
    for ccy, rows in rows_by_ccy.items():
        if ccy == "NZD":
            continue
        result[ccy] = series_for_currency(ccy, list(rows), asof=asof, gate0=gate0.get(ccy))
    nzd_rows = rows_by_ccy.get("NZD") or []
    result["NZD"] = series_for_currency("NZD", list(nzd_rows), asof=asof, gate0=gate0.get("NZD"))
    return result


def parse_history_csv(text):
    """CSV de `fxvol_history.csv` → {divisa: [filas]}. Un blanco se queda vacío, no pasa a 0."""
    grouped = {}
    for row in csv.DictReader(io.StringIO(text)):
        ccy = row["ccy"]
        grouped.setdefault(ccy, []).append({
            "session": row.get("session"),
            "ccy": ccy,
            "status": row.get("status"),
            "reason": row.get("reason"),
            "iv30_atm": row.get("iv30_atm"),
            "rr25": row.get("rr25"),
            "bf25": row.get("bf25"),
            "px_contract": row.get("px_contract"),
            "px_settle": row.get("px_settle"),
        })
    return grouped


def rows_from_builder(hist, days):
    """Adapta la historia en memoria del shadow a las columnas que guarda el CSV.

    Una sesión que no está OK no aporta IV, RR ni BF. El futuro se conserva:
    RV21 mira el settle, no la superficie de opciones.
    """
    grouped = {}
    for ccy, series in hist.items():
        rows = []
        for index, item in enumerate(series):
            future = item.get("fut") or {}
            ok = item.get("status") == "OK"
            rows.append({
                "session": days[index],
                "ccy": ccy,
                "status": item.get("status"),
                "reason": item.get("reason"),
                "iv30_atm": item.get("iv30_atm") if ok else None,
                "rr25": item.get("rr25") if ok else None,
                "bf25": item.get("bf25") if ok else None,
                "px_contract": future.get("px_contract"),
                "px_settle": future.get("px_settle"),
            })
        grouped[ccy] = rows
    return grouped


def publish_states(out, states):
    """Copia solo los campos nuevos. No reescribe claves ya presentes."""
    method = out.setdefault("method", {})
    if "states" not in method:
        method["states"] = METHOD_STATES
    currencies = out.setdefault("currencies", {})
    for ccy, series in states.items():
        if not series:
            continue
        last = series[-1]
        bucket = currencies.setdefault(ccy, {})
        for key in PUBLISHED_FIELDS:
            if key not in bucket:
                bucket[key] = last[key]
    return out

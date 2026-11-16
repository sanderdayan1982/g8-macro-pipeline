#!/usr/bin/env python3
"""FAMILIAS-01 Fase 2, módulo M1 — crédito desde FRED (bloque 13-CRD A), en sombra.

Las series ICE BofA están marcadas en FRED como «Copyrighted: Pre-Approval Required»
(comprobado el 16-nov-2026 en las fichas de las tres series: Percent, Daily, Close;
la reproducción de los valores está prohibida). Este módulo:

  - descarga `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>`
  - calcula en memoria
  - con CRD_PUBLISH en False no escribe nada y no imprime ninguna cifra
    (OAS, z, delta, CRD ni el nombre del régimen)

VOTE = 0. No entra en blocked_entry. No toca lo ya publicado.

Los umbrales son los de la tabla firmada. No hay búsqueda de parámetros.
El [PROV] de umbrales sin calibrar se queda en method.crd hasta un acta de GATE.
"""
import csv
import io
import json
import math
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

# Bandera de publicación. False en este repositorio público: el cálculo vive en
# memoria y no hay fichero. No ponerla a True aquí: el `git add data/` del Daily
# subiría data/credit/ al repo público. El destino de más adelante (PR-C, repo
# privado) son las dos constantes de abajo.
CRD_PUBLISH = False
CRD_SHADOW_PATH = os.path.join("data", "credit", "CRD_shadow.json")
OAS_VINTAGE_PATH = os.path.join("data", "credit", "oas_vintage.csv")

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
# Tres GET seguidos tienen que caber en el tope de 40 s del paso. Un solo intento:
# el reintento largo de g8http (10/40/90 s) no cabe en ese tope y, además, no
# queremos que una traza imprima el cuerpo.
TIMEOUT_SECONDS = 10
USER_AGENT = "g8-macro-pipeline/1.0 (https://github.com/sanderdayan1982/g8-macro-pipeline)"

# Series firmadas. Unidad de FRED: percent. Se guarda en pb (×100).
SERIES_USD_HY = "BAMLH0A0HYM2"
SERIES_USD_IG = "BAMLC0A0CM"       # solo informativa; el CRD de USD sale del HY
SERIES_EUR_HY = "BAMLHE00EHYIOAS"
SERIES_IDS = (SERIES_USD_HY, SERIES_USD_IG, SERIES_EUR_HY)

# GBP JPY AUD NZD CAD CHF no tienen spread FRED en este bloque.
NA_CCYS = ("GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
NA_REASON = "NO_FRED_SPREAD"
CCY_ORDER = ("USD", "EUR") + NA_CCYS

# Tabla firmada. Inclusivo en la entrada, estricto en la salida.
SANO_ENTER = 0.5          # crd >= +0.5
SANO_EXIT = 0.3           # sale si crd < +0.3
TENSO_ENTER = -1.0        # crd <= -1.0
TENSO_EXIT = -0.7         # sale si crd > -0.7
ROTURA_ENTER = -2.0       # crd <= -2.0 y además crd_d1w < 0
ROTURA_EXIT = -1.5        # sale si crd > -1.5
ROTURA_D1W_LT = 0         # la condición de la semana es estricta (< 0)
# 'dd <= p10' no entra en Fase 2.
DD_P10 = "NA"
AISLADO = "UNAVAILABLE"

HIST_MIN = 60
HIST_FULL = 252
DELTA_LAG = 5
DELTA_SPAN = 257          # 252 + 5: z completo en los dos extremos de Δ5
HYSTERESIS_CLOSES = 2
# «older than 2 US business days»: edad 2 sigue clasificando; edad 3 es STALE.
STALE_US_BUSINESS_DAYS = 2
VOTE = 0
BLOCKED_ENTRY = False

# z252 del dashboard (docs/index.html zScore): poblacional ÷N, ventana que
# incluye el valor de hoy. Pine ta.stdev. No es la desviación muestral N−1
# de FXVOL: aquel módulo lo decía explícito y este texto dice z252.
Z_DDOF = 0
Z_SD_FLOOR = 1e-9

WAT = timezone(timedelta(hours=1))   # WAT no tiene horario de verano
REGIMES = ("SANO", "NEUTRO", "TENSO", "ROTURA")
PUBLIC_FIELDS = (
    "oas_hy_bp",
    "oas_ig_bp",
    "oas_asof",
    "oas_z",
    "d_oas_1w",
    "crd",
    "crd_d1w",
    "crd_state",
    "crd_isolated",
    "crd_partial",
    "crd_prov",
    "crd_reason",
    "dd",
)

METHOD_CRD = {
    "shadow": True,
    "vote": VOTE,
    "blocked_entry": BLOCKED_ENTRY,
    "header": "[PROV] umbrales sin calibrar hasta acta de GATE",
    "partial": "[PARCIAL-A]",
    "partial_note": "Fase 2 es solo el bloque A (OAS HY). BK_z entra en Fase 3.",
    "aislado": AISLADO,
    "dd_p10": DD_P10,
    "sign": "crd = -oas_z (R11, una sola vez). Positivo = crédito sano.",
    "usd_input": "HY only",
    "ig": "informative, USD only, outside the CRD formula",
    "source_unit": "percent",
    "store": "bp",
    "z_window": HIST_FULL,
    "z_stdev": "poblacional /N, como zScore del dashboard (Pine ta.stdev)",
    "z_includes_today": True,
    "delta_sessions": DELTA_LAG,
    "delta_span": DELTA_SPAN,
    "hysteresis_closes": HYSTERESIS_CLOSES,
    "stale_older_than_us_business_days": STALE_US_BUSINESS_DAYS,
    "session": "D es la fecha WAT (UTC+1). Usa OAS con fecha < D, nunca OAS(D). Cierre 23:00 WAT.",
    "precedence": ["ROTURA", "TENSO", "SANO", "NEUTRO"],
    "rotura_exit": "TENSO si crd <= -0.7; si no, NEUTRO",
    "holiday_gap": "celda vacía o '.' no es sesión: se excluye, no se rellena",
    "series": {
        "USD_HY": SERIES_USD_HY,
        "USD_IG": SERIES_USD_IG,
        "EUR_HY": SERIES_EUR_HY,
    },
    "na_reason": NA_REASON,
    "thresholds": {
        "SANO_enter_ge": SANO_ENTER,
        "SANO_exit_lt": SANO_EXIT,
        "TENSO_enter_le": TENSO_ENTER,
        "TENSO_exit_gt": TENSO_EXIT,
        "ROTURA_enter_le": ROTURA_ENTER,
        "ROTURA_exit_gt": ROTURA_EXIT,
        "ROTURA_d1w_lt": ROTURA_D1W_LT,
        "dd_p10": DD_P10,
    },
}

VINTAGE_COLUMNS = ("series_id", "obs_date", "value_bp", "rev", "first_seen_utc")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEAK = re.compile(r"\d+\.\d+")


def fred_url(series_id):
    """URL firmada: el mismo endpoint que fetch_sofr, con el id de la serie."""
    return "%s?id=%s" % (FRED_URL, series_id)


def session_date(now):
    """Fecha de la sesión D: el día civil en WAT (UTC+1).

    El cierre de las 23:00 WAT da nombre a esa fecha. Antes y después de las
    23:00, y hasta la medianoche WAT, la etiqueta sigue siendo ese día. OAS con
    fecha D no entra, sea la hora que sea.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(WAT).date()


def _easter_sunday(year):
    """Algoritmo gregoriano anónimo (Meeus/Jones/Butcher). El mismo de votes_ingest."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_us_oas_holiday(day):
    """Festivo que esta serie deja en blanco en FRED.

    Comprobado el 16-nov-2026, solo las fechas (ningún valor): las tres series
    traen la celda vacía en Navidad, Año Nuevo y Viernes Santo cuando caen en
    día laborable. Columbus Day y Veterans Day vienen con cifra, así que cuentan
    como día hábil. Si el festivo cae en fin de semana, este archivo de ~3 años
    no muestra un traslado al viernes o al lunes: no se inventa.
    """
    if (day.month, day.day) in ((1, 1), (12, 25)):
        return True
    return day == _easter_sunday(day.year) - timedelta(days=2)


def is_us_business_day(day):
    return day.weekday() < 5 and not is_us_oas_holiday(day)


def us_business_days_after(asof, session):
    """Días hábiles US estrictamente posteriores a asof, hasta session incluido."""
    if isinstance(asof, str):
        asof = date.fromisoformat(asof[:10])
    if isinstance(session, str):
        session = date.fromisoformat(session[:10])
    if session <= asof:
        return 0
    count = 0
    cursor = asof + timedelta(days=1)
    while cursor <= session:
        if is_us_business_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def _num_token(token):
    """None si el token no es un número. Un vacío no es cero."""
    if token is None:
        return None
    text = str(token).strip()
    if text == "" or text == ".":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_fred_csv(text):
    """Filas del CSV de FRED, sin fines de semana.

    Cada elemento es {date, kind, oas_bp}. kind:
      value — número, ya en pb
      gap   — celda vacía o '.': no es sesión
      bad   — token ilegible en un día que sí está en el archivo
    La primera aparición de una fecha gana (no se reescribe dentro del mismo CSV).
    """
    if not text or "observation_date" not in text.lower() and "date" not in text.lower():
        raise ValueError("PARSE")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError("PARSE")
    seen = set()
    rows = []
    for raw in reader:
        if len(raw) < 2:
            continue
        date_text = raw[0].strip()
        token = raw[1].strip()
        if not date_text:
            continue
        try:
            day = date.fromisoformat(date_text[:10])
        except ValueError:
            continue
        if day.weekday() >= 5 or day in seen:
            continue
        seen.add(day)
        if token == "" or token == ".":
            rows.append({"date": day, "kind": "gap", "oas_bp": None})
            continue
        number = _num_token(token)
        if number is None:
            rows.append({"date": day, "kind": "bad", "oas_bp": None})
            continue
        rows.append({"date": day, "kind": "value", "oas_bp": number * 100.0})
    rows.sort(key=lambda row: row["date"])
    return rows


def sessions_from_parsed(parsed):
    """Rejilla de días hábiles entre la primera y la última fecha del archivo.

    Un gap (vacío o '.') se excluye: no es sesión y no se rellena.
    Un día hábil ausente, que no sea festivo de esta serie, es una sesión sin
    OAS (el hueco se verá como UNAVAILABLE, sin arrastrar el valor anterior).
    Un festivo ausente no es sesión.
    """
    if not parsed:
        return []
    by_date = {row["date"]: row for row in parsed}
    start = min(by_date)
    end = max(by_date)
    out = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            row = by_date.get(cursor)
            if row is None:
                if not is_us_oas_holiday(cursor):
                    out.append({"date": cursor, "oas_bp": None})
            elif row["kind"] == "value":
                out.append({"date": cursor, "oas_bp": row["oas_bp"]})
            elif row["kind"] == "bad":
                out.append({"date": cursor, "oas_bp": None})
            # gap: excluido
        cursor += timedelta(days=1)
    return out


def z252(values):
    """z del último valor sobre las últimas 252 observaciones válidas, hoy incluido.

    Devuelve (z, n). z es None si n < 60 o si no hay dispersión: en ese caso
    el llamador no sustituye por cero.
    """
    n_all = len(values)
    if n_all < HIST_MIN:
        return None, n_all
    window = values[-HIST_FULL:]
    n = len(window)
    mean = sum(window) / float(n)
    var = sum((value - mean) ** 2 for value in window) / float(n)
    if var <= 0.0:
        return None, n
    sd = math.sqrt(var)
    if sd < Z_SD_FLOOR:
        return None, n
    return (window[-1] - mean) / sd, n


def desired_state(state, crd, crd_d1w):
    """Régimen que pide la tabla, dada la posición ya confirmada.

    Precedencia ROTURA > TENSO > SANO > NEUTRO.
    Entrar en ROTURA exige crd_d1w < 0. Salir de ROTURA va a TENSO si
    crd <= -0.7 y a NEUTRO en caso contrario (no salta a SANO en la misma barra).
    """
    rotura = (
        crd <= ROTURA_ENTER
        and crd_d1w is not None
        and crd_d1w < ROTURA_D1W_LT
    )
    tenso = crd <= TENSO_ENTER
    sano = crd >= SANO_ENTER
    if state == "ROTURA":
        if crd > ROTURA_EXIT:
            if crd <= TENSO_EXIT:
                return "TENSO"
            return "NEUTRO"
        return "ROTURA"
    if state == "TENSO":
        if rotura:
            return "ROTURA"
        if crd > TENSO_EXIT:
            if sano:
                return "SANO"
            return "NEUTRO"
        return "TENSO"
    if state == "SANO":
        if rotura:
            return "ROTURA"
        if tenso:
            return "TENSO"
        if crd < SANO_EXIT:
            return "NEUTRO"
        return "SANO"
    if rotura:
        return "ROTURA"
    if tenso:
        return "TENSO"
    if sano:
        return "SANO"
    return "NEUTRO"


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


def advance(state, arm, count, crd, crd_d1w):
    """Un cierre válido. El mismo paso que usa la historia."""
    desired = desired_state(state, crd, crd_d1w)
    return step_hysteresis(state, arm, count, desired)


def _blank_numbers():
    return {
        "oas_hy_bp": None,
        "oas_z": None,
        "d_oas_1w": None,
        "crd": None,
        "crd_d1w": None,
    }


def _point(day, state, reason, prov, numbers, n):
    row = {
        "oas_asof": day.isoformat() if isinstance(day, date) else day,
        "crd_state": state,
        "crd_isolated": AISLADO,
        "crd_partial": True,
        "crd_prov": prov,
        "crd_reason": reason,
        "dd": DD_P10,
        "n": n,
    }
    row.update(numbers)
    return row


def _reason(parts):
    return " ".join(part for part in parts if part)


def walk_sessions(sessions):
    """Estados causales. `sessions` es la rejilla (huecos con oas_bp None), en orden.

    Función pura de esa historia. Un UNAVAILABLE no arrastra valor ni estado,
    y la histéresis vuelve a NEUTRO. Δ5 mira 5 observaciones válidas hacia atrás
    y exige los dos extremos: si uno falta, el delta es None.
    """
    oas_hist = []
    crd_for_oas = []
    state, arm, count = "NEUTRO", None, 0
    out = []
    for row in sessions:
        oas = row.get("oas_bp")
        day = row["date"]
        if oas is None:
            out.append(_point(
                day, "UNAVAILABLE", _reason(["[PARCIAL-A]", "MISSING_OAS"]),
                False, _blank_numbers(), len(oas_hist),
            ))
            state, arm, count = "NEUTRO", None, 0
            continue
        oas_hist.append(oas)
        # Δ5 sobre observaciones válidas (un hueco no es sesión y no cuenta).
        # Los dos extremos tienen que existir; si el extremo viejo no tiene CRD, no hay delta.
        d_oas = None
        if len(oas_hist) > DELTA_LAG:
            d_oas = oas_hist[-1] - oas_hist[-1 - DELTA_LAG]
        z, n = z252(oas_hist)
        if z is None:
            why = "n<60" if n < HIST_MIN else "ZERO_DISP"
            numbers = _blank_numbers()
            numbers["oas_hy_bp"] = oas
            numbers["d_oas_1w"] = d_oas
            out.append(_point(
                day, "UNAVAILABLE", _reason(["[PARCIAL-A]", why]),
                False, numbers, n,
            ))
            state, arm, count = "NEUTRO", None, 0
            crd_for_oas.append(None)
            continue
        crd = -z
        d_crd = None
        if len(crd_for_oas) >= DELTA_LAG and crd_for_oas[-DELTA_LAG] is not None:
            d_crd = crd - crd_for_oas[-DELTA_LAG]
        crd_for_oas.append(crd)
        prov = n < HIST_FULL
        state, arm, count = advance(state, arm, count, crd, d_crd)
        parts = ["[PARCIAL-A]"]
        if prov:
            parts.append("[PROV]")
        out.append(_point(day, state, _reason(parts), prov, {
            "oas_hy_bp": oas,
            "oas_z": z,
            "d_oas_1w": d_oas,
            "crd": crd,
            "crd_d1w": d_crd,
        }, n))
    return out


def evaluate_history(sessions, session):
    """Sesión D: solo observaciones con fecha < D. Nunca el OAS fechado en D.

    Si el último punto de esa historia es un régimen y el as-of tiene más de
    2 días hábiles US, el estado pasa a STALE y no hay clasificación.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session[:10])
    hist = [row for row in sessions if row["date"] < session]
    path = walk_sessions(hist)
    if not path:
        return _point(session, "UNAVAILABLE", _reason(["[PARCIAL-A]", "NO_OAS"]), False, _blank_numbers(), 0)
    last = path[-1]
    if last["oas_hy_bp"] is None:
        return last
    if last["crd_state"] in REGIMES and us_business_days_after(last["oas_asof"], session) > STALE_US_BUSINESS_DAYS:
        covered = dict(last)
        covered["crd_state"] = "STALE"
        covered["crd_prov"] = False
        covered["crd_reason"] = _reason(["[PARCIAL-A]", "STALE"])
        return covered
    return last


def _na_region():
    return {
        "oas_hy_bp": "NA",
        "oas_ig_bp": "NA",
        "oas_asof": "NA",
        "oas_z": "NA",
        "d_oas_1w": "NA",
        "crd": "NA",
        "crd_d1w": "NA",
        "crd_state": "NA",
        "crd_isolated": "NA",
        "crd_partial": "NA",
        "crd_prov": "NA",
        "crd_reason": NA_REASON,
        "dd": "NA",
    }


def _region_from_point(point, oas_ig, extra_reason=""):
    reason = point["crd_reason"]
    if extra_reason:
        reason = _reason([reason, extra_reason])
    return {
        "oas_hy_bp": point["oas_hy_bp"],
        "oas_ig_bp": oas_ig,
        "oas_asof": point["oas_asof"],
        "oas_z": point["oas_z"],
        "d_oas_1w": point["d_oas_1w"],
        "crd": point["crd"],
        "crd_d1w": point["crd_d1w"],
        "crd_state": point["crd_state"],
        "crd_isolated": point["crd_isolated"],
        "crd_partial": point["crd_partial"],
        "crd_prov": point["crd_prov"],
        "crd_reason": reason,
        "dd": point["dd"],
    }


def unavailable_region(reason, oas_ig=None):
    """Región sin cifra y sin estado arrastrado. reason es una palabra, no un cuerpo HTTP."""
    point = _point(None, "UNAVAILABLE", _reason(["[PARCIAL-A]", reason]), False, _blank_numbers(), 0)
    point["oas_asof"] = None
    return _region_from_point(point, oas_ig)


def _ig_on(ig_sessions, asof):
    """IG del mismo día que el HY usado. Si ese día no tiene cifra, None. No se toma otro día."""
    if asof is None:
        return None
    if isinstance(asof, str):
        asof = date.fromisoformat(asof[:10])
    for row in ig_sessions:
        if row["date"] == asof and row["oas_bp"] is not None:
            return row["oas_bp"]
    return None


def valid_points(parsed):
    """Observaciones con cifra, para el archivo de revisiones. Sin huecos."""
    return [
        {"date": row["date"].isoformat(), "value_bp": row["oas_bp"]}
        for row in parsed
        if row["kind"] == "value"
    ]


def merge_vintage(rows, series_id, observations, seen_utc):
    """Archivo append-only. La primera cifra se queda (rev 0, first_seen_utc).

    Si la cifra cambia, se añade una fila nueva con rev = último + 1 (la primera
    revisión es rev 1). No se reescribe la fila vieja ni su first_seen_utc.
    `rows` no se muta.
    """
    grouped = {}
    out = []
    for row in rows:
        copied = dict(row)
        out.append(copied)
        grouped.setdefault((copied["series_id"], copied["obs_date"]), []).append(copied)
    for obs in observations:
        key = (series_id, obs["date"])
        existing = grouped.get(key)
        value = obs["value_bp"]
        if not existing:
            created = {
                "series_id": series_id,
                "obs_date": obs["date"],
                "value_bp": value,
                "rev": 0,
                "first_seen_utc": seen_utc,
            }
            out.append(created)
            grouped[key] = [created]
            continue
        latest = max(existing, key=lambda item: item["rev"])
        if latest["value_bp"] != value:
            created = {
                "series_id": series_id,
                "obs_date": obs["date"],
                "value_bp": value,
                "rev": latest["rev"] + 1,
                "first_seen_utc": seen_utc,
            }
            out.append(created)
            existing.append(created)
    return out


def first_known(rows, series_id):
    """Valor de rev mínima por fecha: el primero que se vio. Las revisiones no pintan el estado."""
    best = {}
    for row in rows:
        if row["series_id"] != series_id:
            continue
        current = best.get(row["obs_date"])
        if current is None or row["rev"] < current["rev"]:
            best[row["obs_date"]] = row
    return {day: row["value_bp"] for day, row in best.items()}


def apply_first_known(sessions, vintage_rows, series_id):
    """Sustituye la cifra descargada por la primera conocida, si el archivo ya la tiene."""
    first = first_known(vintage_rows, series_id)
    out = []
    for row in sessions:
        if row["oas_bp"] is None:
            out.append(dict(row))
            continue
        key = row["date"].isoformat()
        if key in first:
            copied = dict(row)
            copied["oas_bp"] = first[key]
            out.append(copied)
        else:
            out.append(dict(row))
    return out


def _http_reason(status):
    if isinstance(status, int) and status > 0:
        return "HTTP_%d" % status
    return "HTTP_FAIL"


def _series_inputs(status, text, vintage_rows, series_id, seen_utc):
    """(sessions, vintage, error_reason). error_reason None si el CSV se pudo leer."""
    if status != 200:
        return [], vintage_rows, _http_reason(status)
    try:
        parsed = parse_fred_csv(text or "")
    except ValueError:
        return [], vintage_rows, "PARSE"
    points = valid_points(parsed)
    vintage_rows = merge_vintage(vintage_rows, series_id, points, seen_utc)
    sessions = apply_first_known(sessions_from_parsed(parsed), vintage_rows, series_id)
    return sessions, vintage_rows, None


def _count_valid(sessions, session):
    if isinstance(session, str):
        session = date.fromisoformat(session[:10])
    return sum(1 for row in sessions if row["date"] < session and row["oas_bp"] is not None)


def run_shadow(fetched, session, vintage_rows=None, seen_utc="1970-01-01T00:00:00Z"):
    """Documento en memoria + archivo de revisiones en memoria.

    `fetched` mapea el id de serie a (status, csv). El CSV no se registra.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session[:10])
    vintage_rows = list(vintage_rows or [])
    hy_usd, vintage_rows, hy_err = _take(fetched, SERIES_USD_HY, vintage_rows, seen_utc)
    ig_usd, vintage_rows, ig_err = _take(fetched, SERIES_USD_IG, vintage_rows, seen_utc)
    hy_eur, vintage_rows, eur_err = _take(fetched, SERIES_EUR_HY, vintage_rows, seen_utc)
    regions = {}
    counts = {}
    if hy_err:
        regions["USD"] = unavailable_region(hy_err)
        counts["USD"] = 0
    else:
        point = evaluate_history(hy_usd, session)
        ig_value = None if ig_err else _ig_on(ig_usd, point.get("oas_asof"))
        extra = "IG_UNAVAILABLE" if ig_err else ""
        regions["USD"] = _region_from_point(point, ig_value, extra)
        counts["USD"] = _count_valid(hy_usd, session)
    if eur_err:
        regions["EUR"] = unavailable_region(eur_err, oas_ig="NA")
        counts["EUR"] = 0
    else:
        point = evaluate_history(hy_eur, session)
        regions["EUR"] = _region_from_point(point, "NA")
        counts["EUR"] = _count_valid(hy_eur, session)
    for ccy in NA_CCYS:
        regions[ccy] = _na_region()
        counts[ccy] = 0
    doc = {
        "session": session.isoformat(),
        "method": {"crd": METHOD_CRD},
        "regions": regions,
        "counts": counts,
    }
    return doc, vintage_rows


def _take(fetched, series_id, vintage_rows, seen_utc):
    status, text = fetched.get(series_id, (0, ""))
    return _series_inputs(status, text, vintage_rows, series_id, seen_utc)


def shadow_payload(doc):
    """Solo los campos firmados. `counts` no sale: es para el registro, no para el JSON."""
    return {
        "session": doc["session"],
        "method": {"crd": doc["method"]["crd"]},
        "regions": {
            ccy: {key: doc["regions"][ccy][key] for key in PUBLIC_FIELDS}
            for ccy in CCY_ORDER
        },
    }


def render_vintage(rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(VINTAGE_COLUMNS)
    for row in rows:
        writer.writerow([
            row["series_id"],
            row["obs_date"],
            _fmt_bp(row["value_bp"]),
            row["rev"],
            row["first_seen_utc"],
        ])
    return buf.getvalue()


def _fmt_bp(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(float(value), ".10f")


def load_vintage(text):
    if not text or not text.strip():
        return []
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append({
            "series_id": row["series_id"],
            "obs_date": row["obs_date"],
            "value_bp": float(row["value_bp"]),
            "rev": int(row["rev"]),
            "first_seen_utc": row["first_seen_utc"],
        })
    return out


def persist(root, doc, vintage_rows, publish=None):
    """Escribe los dos destinos solo si publish es True. Con False no abre ningún fichero."""
    if publish is None:
        publish = CRD_PUBLISH
    if not publish:
        return False
    shadow = os.path.join(root, CRD_SHADOW_PATH)
    vintage = os.path.join(root, OAS_VINTAGE_PATH)
    os.makedirs(os.path.dirname(shadow), exist_ok=True)
    _atomic(shadow, json.dumps(shadow_payload(doc), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _atomic(vintage, render_vintage(vintage_rows))
    return True


def _atomic(path, text):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".crd-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _public_word(state):
    if state in REGIMES or state == "AISLADO":
        return "OK"
    if state in ("UNAVAILABLE", "STALE", "NA"):
        return state
    return "UNAVAILABLE"


def _why(row):
    if row["crd_state"] == "NA":
        return NA_REASON
    if row["crd_state"] == "STALE":
        return "STALE"
    reason = row["crd_reason"] or ""
    if "HTTP_" in reason or reason.endswith("HTTP_FAIL") or "HTTP_FAIL" in reason:
        return "HTTP"
    if "PARSE" in reason:
        return "PARSE"
    if "MISSING_OAS" in reason or "NO_OAS" in reason:
        return "MISSING"
    if "n<60" in reason or "ZERO_DISP" in reason:
        return "SHORT"
    if row["crd_state"] in REGIMES:
        return "OK"
    return "UNAVAILABLE"


def _leaks(text):
    if _LEAK.search(text):
        return True
    return any(name in text for name in ("SANO", "NEUTRO", "TENSO", "ROTURA", "AISLADO"))


def log_status(doc, emit):
    """Palabras de estado y conteos. Si una línea llevara una cifra o un régimen, no se emite."""
    flag = "on" if CRD_PUBLISH else "off"
    lines = ["CRD_SHADOW publish=%s vote=%s blocked_entry=no" % (flag, VOTE)]
    for ccy in CCY_ORDER:
        row = doc["regions"][ccy]
        partial = row["crd_partial"]
        if partial is True:
            partial_word = "yes"
        elif partial is False:
            partial_word = "no"
        else:
            partial_word = "na"
        lines.append(
            "%s status=%s why=%s partial=%s obs=%d"
            % (ccy, _public_word(row["crd_state"]), _why(row), partial_word, int(doc["counts"][ccy]))
        )
    blob = "\n".join(lines)
    if _leaks(blob):
        emit("CRD_SHADOW status=LOG_BLOCKED")
        return
    emit(blob)


def default_fetch(url, series_id):
    """Un GET. El cuerpo no se imprime. Un error de red es status 0, sin mensaje."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            status = getattr(resp, "status", 200)
            # Se lee para calcular. No se registra.
            text = resp.read().decode("utf-8-sig", "replace")
            return status, text
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return 0, ""


def main(argv=None, fetch=None, now=None, root=None, stdout=None):
    """Sombra. Devuelve 0 también si la fuente falla: el paso no puede tumbar el Daily.

    Con CRD_PUBLISH en False no escribe. stdout recibe solo el registro de estado.
    """
    def emit(line):
        if stdout is None:
            print(line)
        else:
            stdout(line)

    try:
        moment = now or datetime.now(timezone.utc)
        session = session_date(moment)
        getter = fetch or default_fetch
        fetched = {}
        for series_id in SERIES_IDS:
            try:
                fetched[series_id] = getter(fred_url(series_id), series_id)
            except Exception:
                fetched[series_id] = (0, "")
        root = root or REPO
        prior = []
        if CRD_PUBLISH:
            path = os.path.join(root, OAS_VINTAGE_PATH)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    prior = load_vintage(fh.read())
        seen = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc, vintage_rows = run_shadow(fetched, session, vintage_rows=prior, seen_utc=seen)
        log_status(doc, emit)
        persist(root, doc, vintage_rows, publish=CRD_PUBLISH)
        return 0
    except Exception:
        emit("CRD_SHADOW status=ERROR")
        return 0


if __name__ == "__main__":
    sys.exit(main())

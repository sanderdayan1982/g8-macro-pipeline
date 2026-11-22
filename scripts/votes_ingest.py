#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Point-in-time votes for the G8 macro suite (FAMILIAS-00, rule_version 1).

Pure parse: rows_from_messages(messages, session, write_missing) → rows.
Gmail fetch is separate (fetch_messages). Offline:

    python scripts/votes_ingest.py --from-json file.json --session YYYY-MM-DD --dry-run

JSON input is a list of {id, date, subject, body}.

Gmail is read-only. Search query (spam and trash included, nothing modified):

    from:noreply@tradingview.com subject:"Alert:" -subject:"G8-COLLECT"

When the session is known (`run` always has one), that query is limited to the
session window, in Unix seconds (Gmail accepts epoch seconds):

    after:<1D close> before:<05:15 WAT the next morning>

The close is 17:00 America/New_York (`expected_bar_close_ms`): 23:00 WAT in
winter, 22:00 WAT in summer. The cutoff is the 05:15 WAT already named below
(04:15 UTC). Orden Sander 14-nov-2026 16:28 WAT.

A refresh token cannot mint an access token by itself. The token request also
sends GMAIL_OAUTH_CLIENT_ID and GMAIL_OAUTH_CLIENT_SECRET. Scope:
https://www.googleapis.com/auth/gmail.readonly

Attempts (WAT = UTC+1, Africa/Malabo):
  first  04:00 WAT (03:00 UTC) — write complete modules and E21; do not write
         MISSING or INGEST_INCOMPLETE while the final pass is still pending.
  final  05:20 WAT (04:20 UTC) — after the 05:15 WAT cutoff. The final pass
         lists every page of the session mailbox window before it writes
         MISSING, so a mail that arrived before the cutoff is not missed.
         Anything still absent is MISSING. A Gmail failure (missing or invalid
         secrets, auth error, API error after retries) marks every module that
         has no row yet as MISSING (one row per module and currency, voto=NA)
         and sends one Telegram alert.
  A weekend writes no rows. A CME/New York full close (no 1D candle) stores
  mail that arrives, with session_type=HOLIDAY. If no mail arrives, it writes
  nothing (no MISSING, first or final) and logs CLOSED. RELOJES-01 Anexo 1 A1,
  orden expresa de Sander 14-nov-2026 22:00 WAT, t175u 22:08 WAT.
  `attempt_for` still flips at 05:00 WAT. The workflow passes --attempt and
  does not use that clock. That 05:00 flip stays: the existing test
  CloseClock.test_attempt_flips_at_0500_wat is not authorized for this block.

  When the token endpoint returns a non-2xx, the stderr
         line and the Telegram text add the HTTP status and the JSON field
         `error`, and only when that value matches ^[a-z_]{1,40}$; otherwise
         the code is `unknown`. `error_description` and the response body are
         never logged. When a Gmail API read (not the token exchange) fails
         with HTTP 401, or with HTTP 403 for any reason other than
         rateLimitExceeded or userRateLimitExceeded, the same stderr line and
         the Telegram text add the HTTP status, `error.errors[0].reason`, and
         `error.status`. Each value is kept only when it matches
         ^[A-Za-z_]{1,40}$; otherwise it is `unknown`. A missing field or a
         non-JSON body is `none`. A 403 with one of those two reasons, and any
         HTTP 429, are not auth failures: they are retried with bounded
         exponential backoff and jitter. If those retries are exhausted the
         failure is `api`, not `auth`, and the gmail_reason suffix is not
         added. Any other read failure, including 5xx, keeps the previous
         stderr line and Telegram text. `error.message`, the response body,
         headers, and tokens are never logged. E21 from data/USD_FACTOR.json
         is still written.

votes.csv is append-only. A key (fecha_cierre, script, ccy) already present is
not written again. Re-running a session that is already complete appends nothing.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

WAT = ZoneInfo("Africa/Malabo")
NY = ZoneInfo("America/New_York")
UTC = timezone.utc

GMAIL_QUERY = 'from:noreply@tradingview.com subject:"Alert:" -subject:"G8-COLLECT"'
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
RULE_VERSION = "3"  # orden expresa de Sander 22-nov-2026 (t318u): IYDT EXTREME = ±1; filas nuevas solamente
RETRIES = 3
# Pause between consecutive messages.get calls. Orden Sander 14-nov-2026 16:28 WAT.
MESSAGES_GET_PAUSE_S = 0.15
# 403 reason rateLimitExceeded / userRateLimitExceeded, and HTTP 429, are not auth.
# Bounded retries: attempt count, per-sleep cap, and total wait. Same orden.
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_BASE_S = 0.5
RATE_LIMIT_SLEEP_CAP_S = 8.0
RATE_LIMIT_TOTAL_WAIT_S = 20.0
_RATE_LIMIT_REASONS = frozenset(("rateLimitExceeded", "userRateLimitExceeded"))
# 05:15 WAT the morning after the session (04:15 UTC). This is the mailbox
# window's end, already named in this module, docs/VOTES_RULES_v1.md, and
# .github/workflows/votes_ingest.yml. The scheduled final pass is 05:20 WAT
# (04:20 UTC), after this cutoff, and lists every page of the window before
# writing MISSING. attempt_for() is still the 05:00 WAT clock (see the test
# named above). Orden ORDEN_ENZO_REVISION-TRIPLE-01 bloque 2, 14-nov-2026 19:16 WAT.
INGEST_CUTOFF_HOUR_WAT = 5
INGEST_CUTOFF_MINUTE_WAT = 15
# Google's OAuth error code. Anything else, including error_description, is not logged.
_OAUTH_ERROR_RE = re.compile(r"^[a-z_]{1,40}$")
# Gmail API error.errors[0].reason and error.status. error.message is not logged.
_GMAIL_FIELD_RE = re.compile(r"^[A-Za-z_]{1,40}$")

CCYS = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")

# Edición ARGOS v1.0.1 §2 is not in this repo (searched 2026-11-13).
# Family codes are the module short names. E21 is family R, as Sander assigned.
# Replace this map only with an acta.
FAMILIES = {
    "RISK_G8": "RISK",
    "XCCY_G8": "XCCY",
    "XCCY_OIS_SHADOW": "XCCY_OIS",
    "PSI_G8": "PSI",
    "CURVA_G8": "CURVA",
    # Mismo correo que CURVA_G8. Orden Sander 17-nov t239u.
    "CURVA_MOTION": "CURVA",
    "RTF10_G8": "RTF10",
    "POL_G8": "POL",
    "IYDT_2Y": "IYDT",
    "IYDT_10Y": "IYDT",
    "FFVA_G8": "FFVA",
    "TOT_G8": "TOT",
    "EQF_G8": "EQF",
    "POS_G8": "POS",
    "E21": "R",
    # Shadow de crédito. Una fila por divisa, no una fila G8. Orden t235u.
    "CRD_G8": "CRD",
}

# name, version, parts, monolithic. Versions and part counts are the 12-nov-2026 close.
MODULES = (
    ("RISK_G8", "1.2.1", 1, True),
    ("XCCY_G8", "2.8.1", 2, False),
    ("XCCY_OIS_SHADOW", "1.0.0", 1, True),
    ("PSI_G8", "2.4.3", 3, False),
    ("CURVA_G8", "1.4.8", 5, False),
    # Filas de {CCY}_MOTION. No es un correo aparte. Orden Sander 17-nov t239u.
    ("CURVA_MOTION", "1.4.8", 5, False),
    ("RTF10_G8", "3.10.0", 5, False),
    ("POL_G8", "1.3.8", 4, False),
    ("IYDT_2Y", "1.7.2", 8, False),
    ("IYDT_10Y", "1.6.1", 8, False),
    ("FFVA_G8", "1.5.0", 5, False),
    ("TOT_G8", "1.1.3", 5, False),
    ("EQF_G8", "1.1.3", 5, False),
    ("POS_G8", "1.2.6", 6, False),
    # 13-CRD-G8 v1.0.0 SHADOW. Un solo parte (PARTS=1). El flag monolítico
    # es el de RISK: un mensaje, sin ensamblar partes. Orden t235u.
    ("CRD_G8", "1.0.0", 1, True),
)
MODULE = {row[0]: {"version": row[1], "parts": row[2], "monolithic": row[3]} for row in MODULES}
MONOLITHIC = {name for name, spec in MODULE.items() if spec["monolithic"]}

# Public votes.csv: valor stays empty unless the module is listed here.
# Orden Sander 17-nov-2026 t234u. A module enters only when its number comes
# entirely from a public primary source. Out until Sander says otherwise,
# even if a feed looks public: RISK_G8, POL_G8, FFVA_G8, every XCCY id,
# and 13-CRD / CRD_G8. Mail modules whose primary source is not in this repo
# stay out as well.
# E21 is the residual written by scripts/usd_factor.py from ECB EXR reference
# rates (data-api.ecb.europa.eu, series EXR D.*.EUR.SP00.A). The CME and FRED
# twins in that script are not inputs to E21.
ALLOWED_VALOR_MODULES = frozenset({
    "E21",
})

# Bands seen in the 12-nov field list, plus EXTREME (IYDT-2Y v1.7.2 emite
# POS_EXTREME/NEG_EXTREME; orden Sander 22-nov-2026 t318u, rule_version 3).
# Any other band is NA, not STRONG.
IYDT_BANDS = {
    "POS_STRONG": "+1",
    "NEG_STRONG": "-1",
    "POS_BELOW_STRONG": "0",
    "NEG_BELOW_STRONG": "0",
    "POS_EXTREME": "+1",
    "NEG_EXTREME": "-1",
}
ALWAYS_NA = {
    "RISK_G8", "XCCY_G8", "XCCY_OIS_SHADOW", "CURVA_G8", "RTF10_G8",
    "TOT_G8", "EQF_G8", "POS_G8", "PSI_G8",
}
HAS_STATUS = {
    "XCCY_G8", "PSI_G8", "CURVA_G8", "RTF10_G8", "POL_G8",
    "IYDT_2Y", "IYDT_10Y", "FFVA_G8", "TOT_G8", "EQF_G8", "POS_G8",
}
# Fila USD del FFVA (ICEUS:DX1!, fuera de Databento GLBX.MDP3 y de las pruebas de volumen CME).
# El cálculo vivo del FFVA sigue en Pine (9-FFVA); no cambia el voto en vivo.
FFVA_ROW_SOURCE_LABELS = {
    "USD": "FUENTE=ICE · fuera de las pruebas de volumen",
}

# Cabecera publicada antes de E7. Solo esta se migra; otras cabeceras de
# prueba o intermedias no se tocan, para no alterar tests ya existentes.
PRE_E7_COLUMNS = [
    "fecha_cierre", "script", "version", "ccy", "estado", "valor", "voto",
    "stale", "snapshot_id", "hora_recepcion_utc", "id_correo", "bar_close_ms",
    "family", "parts_expected", "parts_received", "rule_version", "session_type",
    "report_date",
]
COLUMNS = [
    "fecha_cierre", "script", "version", "ccy", "estado", "valor", "voto",
    "stale", "snapshot_id", "hora_recepcion_utc", "id_correo", "bar_close_ms",
    "family", "parts_expected", "parts_received", "rule_version",
    "written_at_utc", "inputs_hash", "legacy", "session_type",
    "report_date",
]

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class GmailError(Exception):
    """Failure that marks the session MISSING on the final attempt.

    `kind` is missing_secrets, auth, or api. `status` is an HTTP code or None.
    `oauth_error` is set only for a non-2xx token response: the whitelisted
    `error` code, or `unknown`. `gmail_reason` and `gmail_status` are set only
    for a Gmail API read that fails with HTTP 401 or 403: whitelisted
    `error.errors[0].reason` and `error.status`, `unknown` when a present
    value fails the whitelist, or `none` when the field is missing or the
    body is not JSON. The message never includes a token, client secret,
    error_description, error.message, response body, or header.
    """

    def __init__(self, kind, status=None, oauth_error=None, gmail_reason=None, gmail_status=None):
        self.kind = kind
        self.status = status
        self.oauth_error = oauth_error
        self.gmail_reason = gmail_reason
        self.gmail_status = gmail_status
        label = kind if status is None else "%s HTTP %s" % (kind, status)
        super().__init__(label)


def oauth_error_code(raw):
    """JSON field `error` if it matches ^[a-z_]{1,40}$, otherwise 'unknown'.

    Does not return `error_description` or any other part of the body.
    """
    try:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8")
        elif isinstance(raw, str):
            text = raw
        else:
            return "unknown"
        doc = json.loads(text)
    except (UnicodeError, ValueError, TypeError):
        return "unknown"
    if not isinstance(doc, dict):
        return "unknown"
    code = doc.get("error")
    if isinstance(code, str) and _OAUTH_ERROR_RE.fullmatch(code):
        return code
    return "unknown"


def _gmail_field(value):
    """One Gmail error field. Missing → none. Present but not whitelisted → unknown.

    Does not return the raw value.
    """
    if value is None:
        return "none"
    if isinstance(value, str) and _GMAIL_FIELD_RE.fullmatch(value):
        return value
    return "unknown"


def gmail_api_error_fields(raw):
    """`(reason, status)` from `error.errors[0].reason` and `error.status` only.

    Each value must match ^[A-Za-z_]{1,40}$. Otherwise `unknown`. A missing
    field, or a body that is not a JSON object, is `none`. Does not return
    `error.message` or any other part of the body.
    """
    try:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8")
        elif isinstance(raw, str):
            text = raw
        else:
            return "none", "none"
        doc = json.loads(text)
    except (UnicodeError, ValueError, TypeError):
        return "none", "none"
    if not isinstance(doc, dict):
        return "none", "none"
    err = doc.get("error")
    if not isinstance(err, dict):
        return "none", "none"
    reason = None
    errors = err.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        reason = errors[0].get("reason")
    return _gmail_field(reason), _gmail_field(err.get("status"))


def expected_bar_close_ms(session):
    """FX 1D close is 17:00 America/New_York.

    Winter (EST): 22:00 UTC = 23:00 WAT. Summer (EDT): 21:00 UTC = 22:00 WAT.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session)
    local = datetime(session.year, session.month, session.day, 17, 0, tzinfo=NY)
    return int(local.timestamp() * 1000)


def session_cutoff(session):
    """05:15 WAT the morning after `session` (04:15 UTC).

    The cutoff already named in this file. Not the 05:00 WAT retry in
    `attempt_for`.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session)
    morning = session + timedelta(days=1)
    return datetime(
        morning.year, morning.month, morning.day,
        INGEST_CUTOFF_HOUR_WAT, INGEST_CUTOFF_MINUTE_WAT, tzinfo=WAT,
    )


def gmail_query_for_session(session):
    """Base Gmail query plus after:<1D close> before:<05:15 WAT>, epoch seconds."""
    after_s = expected_bar_close_ms(session) // 1000
    before_s = int(round(session_cutoff(session).timestamp()))
    return "%s after:%d before:%d" % (GMAIL_QUERY, after_s, before_s)


def bar_wat_date(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=WAT).date()


def previous_weekday(today):
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def attempt_for(session, now_utc):
    """final once the 05:00 WAT instant has been reached; otherwise first.

    The scheduled final pass is 05:20 WAT and is selected by the workflow, not
    by this function. This threshold stays at 05:00 WAT.
    """
    final_at = datetime(session.year, session.month, session.day, 4, 0, tzinfo=UTC) + timedelta(days=1)
    return "final" if now_utc >= final_at else "first"


def _nth_weekday(year, month, weekday, n):
    """nth `weekday` (Monday=0) of `month`. `n` is 1-based."""
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed_fixed(day):
    """Saturday → Friday, Sunday → Monday. Weekday stays."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter_sunday(year):
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
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


def ny_full_close_dates(year):
    """Weekdays with no FX 1D candle: NYSE full close, which CME follows for the trade date.

    Computed. Not a stored list. Fixed dates move Saturday → Friday and Sunday → Monday,
    except New Year's Day on Saturday, which NYSE does not observe (announced for
    1 Jan 2028). Good Friday is Easter Sunday minus two days. Early closes
    (the day after Thanksgiving, Christmas Eve) still have a candle and are not included.

    Checked 14-nov-2026 against NYSE Group's 2026–2028 calendar
    (https://ir.theice.com/press/news-details/2025/NYSE-Group-Announces-2026-2027-and-2028-Holiday-and-Early-Closings-Calendar/default.aspx)
    and CME Group holiday hours (https://www.cmegroup.com/trading-hours.html).
    Columbus Day and Veterans Day are federal holidays and are not closes.
    """
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        new_year = new_year + timedelta(days=1)
    elif new_year.weekday() == 5:
        new_year = None
    out = {
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed(date(year, 6, 19)),
        _observed_fixed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed(date(year, 12, 25)),
    }
    if new_year is not None:
        out.add(new_year)
    return out


def is_ny_full_close(session):
    """True when `session` is a CME/New York full close (no 1D candle)."""
    if isinstance(session, str):
        session = date.fromisoformat(session)
    for year in (session.year - 1, session.year, session.year + 1):
        if session in ny_full_close_dates(year):
            return True
    return False


def next_fx_session(session):
    """Siguiente sesión hábil FX después de `session`.

    Reutiliza `is_ny_full_close` (cierre completo CME/Nueva York, sin vela 1D)
    y salta sábados y domingos. Un viernes pasa al lunes, salvo que el lunes
    sea un cierre completo. No inventa festivos: la regla es la ya existente.
    Orden Sander t309u: el cierre t+1 es el 17:00 America/New_York de este día.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session)
    day = session + timedelta(days=1)
    while day.weekday() >= 5 or is_ny_full_close(day):
        day += timedelta(days=1)
    return day


def tplus1_close(session):
    """Datetime UTC del cierre t+1: 17:00 America/New_York de next_fx_session(session)."""
    nxt = next_fx_session(session)
    local = datetime(nxt.year, nxt.month, nxt.day, 17, 0, tzinfo=NY)
    return local.astimezone(UTC)


def _e7_now_z(now):
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_kv_payload(kv):
    """Texto canónico del diccionario KEY=VALUE ya fusionado. No se registra."""
    lines = []
    for key in sorted((kv or {}).keys()):
        val = kv[key]
        if val is None:
            val = ""
        lines.append("%s=%s" % (key, val))
    return "\n".join(lines) + ("\n" if lines else "")


def inputs_hash_for(source, fecha_cierre, script, ccy, **fields):
    """SHA-256 hex (64) del canónico e7.v1. Nunca devuelve ni registra los insumos."""
    lines = [
        "e7.v1",
        "fecha_cierre=%s" % fecha_cierre,
        "script=%s" % script,
        "ccy=%s" % ccy,
        "source=%s" % source,
    ]
    if source == "mail":
        payload = fields.get("payload_kv") or {}
        # Solo el hash del payload entra en el canónico, no el valor.
        payload_digest = hashlib.sha256(_canonical_kv_payload(payload).encode("utf-8")).hexdigest()
        for key in ("version", "snapshot_id", "bar_close_ms", "parts_expected", "parts_received"):
            lines.append("%s=%s" % (key, fields.get(key, "")))
        lines.append("payload=%s" % payload_digest)
    elif source == "usd_factor":
        for key in ("as_of", "generated_utc", "e21"):
            lines.append("%s=%s" % (key, fields.get(key, "")))
    elif source == "missing":
        lines.append("expected_bar_close_ms=%s" % fields.get("expected_bar_close_ms", ""))
    elif source == "incomplete":
        for key in ("reason", "parts_expected", "parts_received"):
            lines.append("%s=%s" % (key, fields.get(key, "")))
    else:
        raise ValueError("source e7 desconocido")
    body = "\n".join(lines) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _ensure_e7_columns(raw):
    """Inserta written_at_utc, inputs_hash, legacy justo antes de session_type.

    Así session_type y report_date siguen siendo las dos últimas columnas
    (tests ya existentes de report_date). Filas antiguas: trazabilidad vacía
    y legacy=1. Si las tres columnas ya están, el fichero no se toca ni un byte.
    """
    if not raw:
        return raw
    text = raw.decode("utf-8")
    if not text:
        return raw
    newline = "\r\n" if "\r\n" in text[:4096] else "\n"
    first, sep, rest = text.partition(newline if newline in text else "\n")
    if not sep:
        first = text
        rest = ""
        sep = ""
    try:
        fields = next(csv.reader([first]))
    except StopIteration:
        return raw
    needed = ["written_at_utc", "inputs_hash", "legacy"]
    if fields == list(COLUMNS) or all(name in fields for name in needed):
        return raw
    # Solo se migra la cabecera oficial previa. Cabeceras cortas de tests no se tocan.
    if fields != list(PRE_E7_COLUMNS):
        return raw
    insert_at = fields.index("session_type")
    new_fields = fields[:insert_at] + needed + fields[insert_at:]
    out_header = ",".join(new_fields)
    out_rows = []
    if rest:
        for line in rest.splitlines():
            if not line:
                out_rows.append(line)
                continue
            try:
                cells = next(csv.reader([line]))
            except StopIteration:
                out_rows.append(line)
                continue
            while len(cells) < len(fields):
                cells.append("")
            cells = cells[:len(fields)]
            # legado: 1; las dos de trazabilidad, vacías.
            cells = cells[:insert_at] + ["", "", "1"] + cells[insert_at:]
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="")
            writer.writerow(cells)
            out_rows.append(buf.getvalue())
    rebuilt = out_header
    if rest or out_rows:
        rebuilt = out_header + newline + newline.join(out_rows)
        if text.endswith(("\n", "\r\n")) and not rebuilt.endswith(("\n", "\r\n")):
            rebuilt += newline
    elif text.endswith(("\n", "\r\n")):
        rebuilt = out_header + newline
    return rebuilt.encode("utf-8")


def after_tplus1_close(fecha_cierre, written_at, now=None):
    """True si written_at (o now) es posterior al cierre t+1 de fecha_cierre."""
    deadline = tplus1_close(fecha_cierre)
    if written_at:
        stamp = written_at
        if stamp.endswith("Z"):
            stamp = stamp[:-1] + "+00:00"
        when = datetime.fromisoformat(stamp)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    else:
        when = now or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    return when > deadline


def register_rejected(path, row, deadline, written_at, reason="AFTER_TPLUS1_CLOSE"):
    """Anota el rechazo. Solo clave, hora, plazo y motivo. Sin insumos ni cuerpos."""
    rec = {
        "fecha_cierre": row.get("fecha_cierre", ""),
        "script": row.get("script", ""),
        "ccy": row.get("ccy", ""),
        "written_at_utc": written_at,
        "deadline_utc": deadline.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": reason,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")


def parse_when(value):
    """ISO-8601, RFC2822, or epoch milliseconds → UTC 'YYYY-MM-DDTHH:MM:SSZ'."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit():
        ms = int(text)
        if ms > 10_000_000_000:
            ms = ms / 1000.0
        return datetime.fromtimestamp(ms, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, IndexError):
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_payload(body):
    """Pipe-separated KEY=VALUE after the G8| prefix. None if the body is not a vote mail."""
    if not body:
        return None
    start = body.find("G8|")
    if start < 0:
        return None
    line = body[start:].splitlines()[0].strip()
    bits = line.split("|")
    if not bits or bits[0] != "G8":
        return None
    out = {}
    for bit in bits[1:]:
        if "=" not in bit:
            continue
        key, val = bit.split("=", 1)
        key = key.strip()
        if key:
            out[key] = val.strip()
    return out or None


def _ccys_for(name):
    return ("G8",) if name == "RISK_G8" else CCYS


def _status_key(name, ccy):
    if name not in HAS_STATUS:
        return None
    if name == "POS_G8":
        return "%s_LF_STATUS" % ccy
    if name == "IYDT_2Y":
        return "Y2_%s_STATUS" % ccy
    if name == "IYDT_10Y":
        return "Y10_%s_STATUS" % ccy
    if name == "TOT_G8":
        return "TOT_%s_STATUS" % ccy
    if name == "EQF_G8":
        return "EQF_%s_STATUS" % ccy
    return "%s_STATUS" % ccy


def _public_valor(name, valor):
    """Drop valor unless the module is on the public whitelist."""
    if name in ALLOWED_VALOR_MODULES:
        return valor
    return ""


def _is_rtf10_v4(kv):
    """ALERT_VERSION=4 es el esquema nuevo. Ausente o 3 = histórico v3."""
    return (kv.get("ALERT_VERSION") or "").strip() == "4"


def _rtf10_fresh_n(kv):
    """Cuántas de las 8 divisas traen {CCY}_STATUS=FRESH.

    En RTF10_G8_v4.0.0.pine, f_status / f_worst emiten exactamente
    FRESH, STALE o NA (peor de 10Y, 2Y y FX). Solo FRESH cuenta.
    """
    n = 0
    for ccy in CCYS:
        if (kv.get("%s_STATUS" % ccy) or "").strip() == "FRESH":
            n += 1
    return n


def _rtf10_v4_health_estado(kv):
    """Estado de presentación (salud del dato). No usa READING ni DRIVER.

    t339u, orden expresa Sander 22-nov:
      OK       = BATCH_STATUS=DATA y ≥6/8 divisas FRESH
      DEGRADED = BATCH_STATUS=DATA y <6/8 FRESH
      ERROR    = paquete BATCH_STATUS=ERROR
      STALE    = no hay correo v4 al cierre D1 (ver _rtf10_stale_if_no_v4)
    """
    batch = (kv.get("BATCH_STATUS") or "").strip()
    if batch == "ERROR":
        return "ERROR"
    if batch == "DATA":
        return "OK" if _rtf10_fresh_n(kv) >= 6 else "DEGRADED"
    return None


def _rtf10_version_is_v4(version):
    """SCRIPT_VERSION 4.x (hoy 4.0.0). No cuenta 3.10.0 ni un STALE placeholder."""
    return (version or "").strip().startswith("4")


def first_rtf10_v4_session_from_rows(rows):
    """Fecha del primer correo v4, derivada de la historia (votes.csv).

    No está hardcodeada. Solo filas RTF10_G8 con version 4.x (un v4 ya
    ingerido: OK / DEGRADED / ERROR). Un STALE posterior se escribe con
    la versión de catálogo 3.10.0 y no mueve esta fecha.
    """
    first = None
    for row in rows or []:
        if row.get("script") != "RTF10_G8":
            continue
        if not _rtf10_version_is_v4(row.get("version")):
            continue
        day = (row.get("fecha_cierre") or "").strip()
        if not day:
            continue
        if first is None or day < first:
            first = day
    return date.fromisoformat(first) if first else None


def first_rtf10_v4_session_from_messages(messages, session):
    """Si el lote actual trae un correo v4, esa sesión también cuenta."""
    if isinstance(session, str):
        session = date.fromisoformat(session)
    for mail in messages or []:
        kv = parse_payload(mail.get("body") or "")
        if kv and kv.get("MODULE") == "RTF10_G8" and _is_rtf10_v4(kv):
            return session
    return None


def first_rtf10_v4_session_from_votes_path(path):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, encoding="utf-8", newline="") as fh:
        return first_rtf10_v4_session_from_rows(csv.DictReader(fh))


def resolve_first_rtf10_v4_session(history_rows, messages, session, history_date=None):
    hist = history_date if history_date is not None else first_rtf10_v4_session_from_rows(history_rows)
    current = first_rtf10_v4_session_from_messages(messages, session)
    if hist and current:
        return hist if hist <= current else current
    return hist or current


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _rtf10_v4_regime(session, first_v4_session):
    """STALE solo rige desde la fecha del primer correo v4 recibido."""
    first = _as_date(first_v4_session)
    if first is None:
        return False
    return _as_date(session) >= first


def _rtf10_stale_if_no_v4(write_missing, session=None, first_v4_session=None):
    """STALE en el intento final (05:20 WAT, tras expected_bar_close_ms y el corte 05:15).

    t340u, orden expresa Sander 23-nov: solo desde la fecha del primer
    correo v4 recibido (historia persistida en votes.csv, no hardcodeada).
    Los días anteriores quedan MISSING / DRIVER como estaban.
    """
    if not write_missing or not _rtf10_v4_regime(session, first_v4_session):
        return None
    return "STALE"


def _rtf10_stale_rows(session, *, snap_id="", received_at="", mail_ids="",
                      bar_ms=None, parts_n="", received=0, version=""):
    expected = expected_bar_close_ms(session)
    if bar_ms is None:
        bar_ms = expected
    bar_stale = bar_ms != expected
    version = version or MODULE["RTF10_G8"]["version"]
    parts_expected = parts_n if parts_n != "" else MODULE["RTF10_G8"]["parts"]
    rows = []
    for ccy in _ccys_for("RTF10_G8"):
        rows.append(_with_hash(_blank_row(
            session, "RTF10_G8", ccy, "STALE", "NA", bar_stale, snap_id,
            received_at, mail_ids, bar_ms, parts_expected, received, version,
        ), "missing", expected_bar_close_ms=expected))
    return rows


def _estado_valor(name, ccy, kv):
    estado, valor = _estado_valor_from_mail(name, ccy, kv)
    return estado, _public_valor(name, valor)


def _estado_valor_from_mail(name, ccy, kv):
    if name == "RISK_G8":
        return kv.get("REGIME", ""), kv.get("SCORE", "")
    if name == "XCCY_G8":
        return kv.get("%s_REGIME" % ccy, ""), ""
    if name == "XCCY_OIS_SHADOW":
        return kv.get("%s_REGIME" % ccy, ""), ""
    if name == "PSI_G8":
        return kv.get("%s_HEALTH" % ccy, ""), kv.get("%s_P_HEALTHY_BP" % ccy, "")
    if name == "CURVA_G8":
        return kv.get("%s_REGIME" % ccy, ""), kv.get("%s_SPREAD_BP" % ccy, "")
    if name == "CURVA_MOTION":
        # Estado de movimiento. Valor siempre vacío. Orden Sander 17-nov t239u.
        return kv.get("%s_MOTION" % ccy, ""), ""
    if name == "RTF10_G8":
        # t339u, orden expresa Sander 22-nov: en v4 el estado es salud del
        # dato (OK / DEGRADED / ERROR), no dirección. No se deriva de
        # READING ni READING_MAIN. {CCY}_STATUS del pine es FRESH / STALE / NA.
        # v3 (ALERT_VERSION ausente o 3) sigue leyendo DRIVER, histórico,
        # cuando no hay v4.
        if _is_rtf10_v4(kv):
            health = _rtf10_v4_health_estado(kv)
            return (health if health is not None else "NA"), ""
        return kv.get("%s_DRIVER" % ccy, ""), ""
    if name == "POL_G8":
        state = kv.get("%s_STATE" % ccy, "") or kv.get("%s_NOW_DIRECTION" % ccy, "")
        return state, kv.get("%s_HAWK_SCORE" % ccy, "")
    if name == "IYDT_2Y":
        return kv.get("Y2_%s_SCORE_BAND" % ccy, ""), kv.get("Y2_%s_SCORE" % ccy, "")
    if name == "IYDT_10Y":
        return kv.get("Y10_%s_SCORE_BAND" % ccy, ""), kv.get("Y10_%s_SCORE" % ccy, "")
    if name == "FFVA_G8":
        return kv.get("%s_REGIME" % ccy, ""), _ffva_valor(kv, ccy)
    if name == "TOT_G8":
        return kv.get("TOT_%s_REGIME" % ccy, ""), kv.get("TOT_%s_SCORE" % ccy, "")
    if name == "EQF_G8":
        return kv.get("EQF_%s_REGIME" % ccy, ""), kv.get("EQF_%s_SCORE" % ccy, "")
    if name == "POS_G8":
        return kv.get("%s_LF_STATE" % ccy, ""), kv.get("%s_LF_Z" % ccy, "")
    if name == "CRD_G8":
        # Solo el estado por divisa. El resto del correo no entra en valor.
        return kv.get("CRD_%s_STATE" % ccy, ""), ""
    return "", ""


def _ffva_valor(kv, ccy):
    direct = kv.get("%s_DIRECTIONAL_INTENSITY" % ccy, "")
    if direct != "":
        return direct
    for key in ("%s_Z_FLOW" % ccy, "%s_ZFLOW" % ccy, "Z_FLOW_%s" % ccy, "%s_Z" % ccy):
        if kv.get(key, "") != "":
            return kv[key]
    for key, val in kv.items():
        if val != "" and "Z_FLOW" in key and ccy in key:
            return val
    return ""


def _vote(name, ccy, kv, estado, status, bar_stale):
    """→ (voto, stale). NA is never rewritten to 0.

    FRESH_PROXY is fresh for the stale flag only. IYDT_2Y, IYDT_10Y, FFVA_G8
    and POL_G8 keep voto NA, the same as a non-FRESH status did before.
    Orden de Sander 14-nov-2026 (t159u).
    """
    proxy = status == "FRESH_PROXY"
    stale = bool(bar_stale) or (status is not None and status not in ("FRESH", "FRESH_PROXY"))
    if name == "CRD_G8":
        # Shadow: el voto es 0 y no se cancela. Orden t235u.
        return "0", stale
    if name == "CURVA_MOTION":
        # Voto NA siempre. Orden Sander 17-nov t239u.
        return "NA", stale
    if name in ALWAYS_NA:
        return "NA", stale
    if name in ("IYDT_2Y", "IYDT_10Y"):
        if stale or proxy:
            return "NA", stale
        return IYDT_BANDS.get(estado, "NA"), False
    if name == "FFVA_G8":
        # Para USD (ICEUS:DX1!): ver FFVA_ROW_SOURCE_LABELS["USD"]; no altera el voto en vivo.
        roll = kv.get("%s_ROLL" % ccy, "")
        if stale or proxy or roll == "YES" or estado == "STALE":
            return "NA", stale or estado == "STALE"
        if estado in ("ACCUM", "SC"):
            return "+1", False
        if estado in ("DIST", "LIQ"):
            return "-1", False
        if estado == "FLAT_OI":
            return "0", False
        return "NA", False
    if name == "POL_G8":
        eligible = kv.get("%s_CLASS_ELIGIBLE" % ccy, "")
        counted = kv.get("%s_REPRICE_COUNTED" % ccy, "")
        direction = kv.get("%s_NOW_DIRECTION" % ccy, "")
        if stale or proxy or eligible != "YES":
            return "NA", stale
        if counted == "NONE":
            return "0", False
        if counted in ("FULL", "PATH") and direction == "HAWKISH":
            return "+1", False
        if counted in ("FULL", "PATH") and direction == "DOVISH":
            return "-1", False
        return "NA", False
    return "NA", stale


def _blank_row(session, name, ccy, estado, voto, stale, snapshot_id, received_at,
               mail_ids, bar_ms, parts_expected, parts_received, version, valor="",
               written_at="", inputs_hash="", legacy=""):
    return {
        "fecha_cierre": session.isoformat(),
        "script": name,
        "version": version,
        "ccy": ccy,
        "estado": estado,
        "valor": valor,
        "voto": voto,
        "stale": "true" if stale else "false",
        "snapshot_id": snapshot_id,
        "hora_recepcion_utc": received_at,
        "id_correo": mail_ids,
        "bar_close_ms": "" if bar_ms is None else str(bar_ms),
        "family": FAMILIES[name],
        "parts_expected": str(parts_expected),
        "parts_received": str(parts_received),
        "rule_version": RULE_VERSION,
        "written_at_utc": written_at,
        "inputs_hash": inputs_hash,
        "legacy": legacy,
        # Non-holiday rows the ingestor writes are REGULAR. A holiday with mail
        # overwrites this to HOLIDAY. RELOJES-01 Anexo 1 A1.
        "session_type": "REGULAR",
        # Orden Sander 17-nov t237u. Weekly modules (dealers and Japan/MoF
        # flows) should carry the publication date of the print. Daily
        # modules stay empty. This parser has no weekly-module list and no
        # publication-date field, so the cell is left empty rather than
        # copied from fecha_cierre.
        "report_date": "",
    }


def _with_hash(row, source, **fields):
    """Rellena inputs_hash. No registra ni devuelve los insumos."""
    row["inputs_hash"] = inputs_hash_for(
        source, row["fecha_cierre"], row["script"], row["ccy"], **fields
    )
    return row


def missing_rows(session, names=None):
    """One MISSING row per module and its currencies. voto is NA. Nothing is filled with 0."""
    rows = []
    expected = expected_bar_close_ms(session)
    for name, _version, parts, _mono in MODULES:
        if names is not None and name not in names:
            continue
        for ccy in _ccys_for(name):
            rows.append(_with_hash(_blank_row(
                session, name, ccy, "MISSING", "NA", False, "", "", "",
                expected, parts, 0, MODULE[name]["version"],
            ), "missing", expected_bar_close_ms=expected))
    return rows


def _sent_ms(kv):
    raw = kv.get("SENT_MS", "")
    return int(raw) if str(raw).isdigit() else -1


# ASCII integer of 1 or 2 digits, then a range check. Orden t159u.
_ASCII_PART_RE = re.compile(r"^[0-9]{1,2}$")
_PARTS_MAX = 64


def _ascii_part_int(raw):
    """1–2 ASCII digits, or None. Rejects signs, garbage, and non-ASCII digits."""
    if isinstance(raw, bool) or not isinstance(raw, str):
        return None
    if _ASCII_PART_RE.fullmatch(raw) is None:
        return None
    return int(raw)


def _declared_parts_n(raw):
    """PARTS as an ASCII integer in 1..64, or None."""
    n = _ascii_part_int(raw)
    if n is None or n < 1 or n > _PARTS_MAX:
        return None
    return n


def mail_id_digest(gmail_id):
    """sha256 hex of a Gmail message id, first 16 characters. Never the raw id."""
    return hashlib.sha256(gmail_id.encode("utf-8")).hexdigest()[:16]


def format_mail_ids(raw_ids):
    """Each id hashed, then sorted and joined with ';'. Empty stays empty."""
    digests = [mail_id_digest(item) for item in raw_ids if item]
    return ";".join(sorted(set(digests)))


def _payload_line(body):
    """The G8 payload line, without a trailing CR. Empty when the body is not a vote mail."""
    if not body:
        return ""
    start = body.find("G8|")
    if start < 0:
        return ""
    line = body[start:].splitlines()[0]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _merge_parts(parts):
    """→ (merged dict, conflict). PART and MESSAGE_ID differ by part; that is not a conflict."""
    merged = {}
    conflict = False
    for kv in parts:
        for key, val in kv.items():
            if key in ("PART", "MESSAGE_ID"):
                continue
            if key in merged and merged[key] != val:
                conflict = True
            else:
                merged.setdefault(key, val)
    return merged, conflict


def _span_exact(numbers, n):
    """True when `numbers` is exactly the integers 1..n, once each.

    Count, min, max, and uniqueness. Does not allocate a list of size n.
    """
    seen = set()
    lo = None
    hi = None
    for number in numbers:
        if number in seen:
            return False
        seen.add(number)
        if lo is None or number < lo:
            lo = number
        if hi is None or number > hi:
            hi = number
    return len(seen) == n and lo == 1 and hi == n


def _missing_parts(seen, n):
    """Part numbers in 1..n that were not received. n is at most 64."""
    missing = []
    i = 1
    while i <= n:
        if i not in seen:
            missing.append(i)
        i += 1
    return missing


def _snapshot_incomplete_reason(name, part_items):
    """→ (incomplete, merged, received, declared_n, detail).

    `part_items` is a list of (mail, kv, payload_line). Duplicate PART copies
    are compared on that line before any MESSAGE_ID dedup. declared_n is the
    PARTS integer when every copy declares the same value in 1..64.
    Monolithic modules do not declare PARTS; declared_n is the reference count.
    """
    spec = MODULE[name]
    empty = {"missing": [], "extra": [], "reason": ""}
    if spec["monolithic"]:
        kv = part_items[-1][1]
        # El parte único de CRD_G8 puede llegar con BATCH_STATUS=ERROR
        # (límite de tamaño). Mismo desenlace que el resto de módulos. Orden t235u.
        if name == "CRD_G8" and kv.get("BATCH_STATUS") == "ERROR":
            return True, kv, 0, spec["parts"], {
                "missing": [], "extra": [], "reason": "batch_error",
            }
        return False, kv, 1, spec["parts"], empty
    transport_bad = False
    batch_error = False
    dup_conflict = False
    by_part = {}
    for mail, kv, line in part_items:
        if kv.get("TRANSPORT") != "G8_PARTS_V1" or kv.get("TRANSPORT_REV") not in ("1.2", "1.3"):
            transport_bad = True
        if kv.get("BATCH_STATUS") == "ERROR":
            batch_error = True
        key = kv.get("PART", "")
        if not isinstance(key, str):
            key = str(key)
        bucket = by_part.setdefault(key, [])
        if bucket and any(prev != line for _m, _kv, prev in bucket):
            dup_conflict = True
        elif not bucket:
            pass
        bucket.append((mail, kv, line))
    # Identical copies collapse to one. A differing copy was flagged above and
    # is not allowed to replace the earlier one.
    unique = []
    for copies in by_part.values():
        unique.append(copies[0])
    declared = None
    parts_bad = False
    parts_disagree = False
    for _mail, kv, _line in unique:
        n = _declared_parts_n(kv.get("PARTS", ""))
        if n is None:
            parts_bad = True
        elif declared is None:
            declared = n
        elif declared != n:
            parts_disagree = True
    if parts_bad or parts_disagree:
        declared_n = None
    else:
        declared_n = declared
    extra = []
    bad_part = False
    in_range = []
    for _mail, kv, _line in unique:
        number = _ascii_part_int(kv.get("PART", ""))
        if number is None:
            bad_part = True
            continue
        if declared_n is None:
            continue
        if number < 1 or number > declared_n:
            extra.append(number)
        else:
            in_range.append(number)
    extra = sorted(set(extra))
    seen = set(in_range)
    missing = _missing_parts(seen, declared_n) if declared_n is not None else []
    span_ok = (
        declared_n is not None and not extra and not bad_part
        and _span_exact(in_range, declared_n)
    )
    merged, conflict = _merge_parts([kv for _mail, kv, _line in unique])
    if dup_conflict:
        reason = "dup_conflict"
    elif parts_bad:
        reason = "bad_parts"
    elif parts_disagree:
        reason = "parts_disagree"
    elif bad_part:
        reason = "bad_part"
    elif transport_bad:
        reason = "transport"
    elif batch_error:
        reason = "batch_error"
    elif conflict:
        reason = "conflict"
    elif missing and extra:
        reason = "gap"
    elif extra:
        reason = "extra"
    elif missing or not span_ok:
        reason = "missing"
    else:
        reason = ""
    bad = reason != ""
    received = len(seen)
    detail = {"missing": missing, "extra": extra, "reason": reason}
    return bad, merged, received, declared_n, detail


def rows_from_messages(messages, session, write_missing, first_rtf10_v4_session=None):
    """Bodies in, rows out. No network and no disk.

    `write_missing` is true only on the final attempt (scheduled 05:20 WAT,
    after the 05:15 WAT cutoff, and only after every page of the mailbox
    window has been listed). Until then, absent or incomplete modules are
    left unwritten so the final pass can still complete them.

    `first_rtf10_v4_session` is the date of the first v4 email already
    received (from votes.csv history and/or this batch). It is not
    hardcoded. Before that date, RTF10 stays MISSING / DRIVER.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session)
    expected = expected_bar_close_ms(session)
    first_rtf10_v4_session = resolve_first_rtf10_v4_session(
        None, messages, session, history_date=first_rtf10_v4_session,
    )
    v4_regime = _rtf10_v4_regime(session, first_rtf10_v4_session)
    # module → bar_ms → snapshot_id → list[(mail, kv)]
    grouped = {}
    mono = {}
    for mail in messages or []:
        kv = parse_payload(mail.get("body") or "")
        if not kv:
            continue
        name = kv.get("MODULE", "")
        if name not in MODULE:
            continue
        bar_raw = kv.get("BAR_CLOSE_MS", "")
        if not str(bar_raw).isdigit():
            continue
        bar_ms = int(bar_raw)
        if bar_wat_date(bar_ms) != session and bar_ms != expected:
            continue
        mail_id = str(mail.get("id") or "")
        when = parse_when(mail.get("date"))
        item = ({"id": mail_id, "date": when}, kv, _payload_line(mail.get("body") or ""))
        if name in MONOLITHIC:
            key = (name, bar_ms)
            prev = mono.get(key)
            if prev is None or _sent_ms(kv) >= _sent_ms(prev[1]):
                mono[key] = item
            continue
        # Keep every copy. Duplicate PART content is judged before MESSAGE_ID dedup.
        snap = kv.get("SNAPSHOT_ID") or "%s:%s:%s" % (name, bar_ms, _sent_ms(kv))
        grouped.setdefault(name, {}).setdefault(bar_ms, {}).setdefault(snap, []).append(item)

    chosen = {}
    for (name, bar_ms), item in mono.items():
        chosen[name] = (bar_ms, [item], item[1].get("SNAPSHOT_ID") or "%s:%s" % (name, bar_ms))
    for name, bars in grouped.items():
        best = None
        for bar_ms, snaps in bars.items():
            for snap_id, items in snaps.items():
                bad, merged, _received, _declared, _detail = _snapshot_incomplete_reason(name, items)
                sent = max(_sent_ms(kv) for _m, kv, _line in items)
                # Prefer a complete snapshot, then the later SENT_MS, then snap_id.
                # t339u, orden expresa Sander 22-nov: en RTF10, el estado lo
                # escribe solo v4. Un v3 posterior el mismo día no lo pisa.
                if name == "RTF10_G8":
                    is_v4 = 1 if _is_rtf10_v4(merged) else 0
                    rank = (is_v4, 0 if bad else 1, sent, snap_id)
                else:
                    rank = (0 if bad else 1, sent, snap_id)
                if best is None or rank > best[0]:
                    best = (rank, bar_ms, items, snap_id)
        if best:
            chosen[name] = (best[1], best[2], best[3])

    rows = []
    present = set()
    for name, _version, _parts, _mono in MODULES:
        if name not in chosen:
            continue
        present.add(name)
        bar_ms, items, snap_id = chosen[name]
        bad, merged, received, declared_n, detail = _snapshot_incomplete_reason(name, items)
        mail_ids = format_mail_ids(m["id"] for m, _kv, _line in items)
        received_at = max((m["date"] for m, _kv, _line in items if m["date"]), default="")
        version = merged.get("SCRIPT_VERSION") or MODULE[name]["version"]
        bar_stale = bar_ms != expected
        # Catalog count stays a reference. Rows use the PARTS the mails declared.
        parts_n = "" if declared_n is None else declared_n
        # t340u: desde el primer v4, un día con solo v3 no escribe DRIVER
        # (votes.csv es append-only). En el final, estado=STALE.
        if (name == "RTF10_G8" and v4_regime and not _is_rtf10_v4(merged)):
            if not write_missing:
                present.discard(name)
                continue
            rows.extend(_rtf10_stale_rows(
                session, snap_id=snap_id, received_at=received_at,
                mail_ids=mail_ids, bar_ms=bar_ms, parts_n=parts_n,
                received=received, version=version,
            ))
            continue
        if bad:
            # t339u: un paquete v4 con BATCH_STATUS=ERROR es el estado ERROR,
            # no INGEST_INCOMPLETE (faltan partes). Se escribe ya en el
            # primer intento: el paquete es definitivo.
            if (name == "RTF10_G8" and _is_rtf10_v4(merged)
                    and (merged.get("BATCH_STATUS") or "").strip() == "ERROR"):
                for ccy in _ccys_for(name):
                    rows.append(_with_hash(_blank_row(
                        session, name, ccy, "ERROR", "NA", bar_stale, snap_id,
                        received_at, mail_ids, bar_ms, parts_n, received, version,
                    ), "mail", version=version, snapshot_id=snap_id,
                        bar_close_ms=bar_ms, parts_expected=parts_n,
                        parts_received=received, payload_kv=merged))
                continue
            if not write_missing:
                present.discard(name)
                print(
                    "skip %s incomplete declared=%s missing=%s extra=%s reason=%s" % (
                        name,
                        "none" if declared_n is None else declared_n,
                        detail["missing"],
                        detail["extra"],
                        detail["reason"],
                    ),
                    file=sys.stderr,
                )
                continue
            for ccy in _ccys_for(name):
                rows.append(_with_hash(_blank_row(
                    session, name, ccy, "INGEST_INCOMPLETE", "NA", bar_stale, snap_id,
                    received_at, mail_ids, bar_ms, parts_n, received, version,
                ), "incomplete", reason=detail.get("reason", ""),
                    parts_expected=parts_n, parts_received=received))
            continue
        for ccy in _ccys_for(name):
            estado, valor = _estado_valor(name, ccy, merged)
            status_key = _status_key(name, ccy)
            if status_key:
                status = merged.get(status_key)
                if status is None:
                    status = ""
            else:
                status = None
            voto, stale = _vote(name, ccy, merged, estado, status, bar_stale)
            rows.append(_with_hash(_blank_row(
                session, name, ccy, estado, voto, stale, snap_id, received_at, mail_ids,
                bar_ms, parts_n, received if name not in MONOLITHIC else 1, version, valor,
            ), "mail", version=version, snapshot_id=snap_id, bar_close_ms=bar_ms,
                parts_expected=parts_n, parts_received=received if name not in MONOLITHIC else 1,
                payload_kv=merged))
        if name == "CURVA_G8":
            # {CCY}_MOTION viaja en el mismo correo. No es otro módulo de partes.
            # Orden Sander 17-nov t239u.
            present.add("CURVA_MOTION")
            got_n = received if name not in MONOLITHIC else 1
            for ccy in _ccys_for("CURVA_MOTION"):
                motion = _with_hash(_blank_row(
                    session, "CURVA_MOTION", ccy, merged.get("%s_MOTION" % ccy, ""),
                    "NA", bar_stale, snap_id, received_at, mail_ids, bar_ms,
                    parts_n, got_n, version, "",
                ), "mail", version=version, snapshot_id=snap_id, bar_close_ms=bar_ms,
                    parts_expected=parts_n, parts_received=got_n, payload_kv=merged)
                motion["report_date"] = ""
                rows.append(motion)
    if write_missing:
        missing_names = [n for n, *_ in MODULES if n not in present]
        if v4_regime and "RTF10_G8" in missing_names:
            missing_names = [n for n in missing_names if n != "RTF10_G8"]
            rows.extend(_rtf10_stale_rows(session))
        rows.extend(missing_rows(session, names=missing_names))
    return rows


def factor_is_fresh(doc, session):
    """Stale when as_of is older than fecha_cierre.

    The file stores one snapshot (as_of + generated_utc). Also stale when
    as_of is newer than the session (that print is not this session) or when
    generated_utc falls on a calendar day before the session.
    """
    as_of = str((doc or {}).get("as_of") or "")
    generated = str((doc or {}).get("generated_utc") or "")
    if as_of != session.isoformat():
        return False
    if generated and generated[:10] < session.isoformat():
        return False
    return True


def _sign(number):
    if number > 0:
        return "+1"
    if number < 0:
        return "-1"
    return "0"


def _num_text(number):
    try:
        dec = Decimal(str(number))
    except InvalidOperation:
        return str(number)
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def rows_from_factor(doc, session):
    """E21 rows for currencies[] in USD_FACTOR.json. USD is not in that list."""
    if isinstance(session, str):
        session = date.fromisoformat(session)
    if not isinstance(doc, dict):
        return []
    fresh = factor_is_fresh(doc, session)
    version = str(doc.get("version") or "")
    generated = parse_when(doc.get("generated_utc"))
    as_of = str(doc.get("as_of") or "")
    snap = "E21:%s" % as_of if as_of else "E21"
    bar = expected_bar_close_ms(session)
    rows = []
    for entry in doc.get("currencies") or []:
        if not isinstance(entry, dict):
            continue
        ccy = entry.get("ccy")
        if ccy not in CCYS:
            continue
        raw = entry.get("E21")
        if raw is None or not fresh:
            rows.append(_with_hash(_blank_row(
                session, "E21", ccy, "STALE" if raw is not None else "MISSING", "NA", True,
                snap, generated, "", bar, 1, 1, version,
                _public_valor("E21", "" if raw is None else _num_text(raw)),
            ), "usd_factor", as_of=as_of, generated_utc=generated,
                e21="" if raw is None else _num_text(raw)))
            continue
        try:
            number = Decimal(str(raw))
        except InvalidOperation:
            rows.append(_with_hash(_blank_row(
                session, "E21", ccy, "STALE", "NA", True, snap, generated, "", bar, 1, 1, version,
                _public_valor("E21", ""),
            ), "usd_factor", as_of=as_of, generated_utc=generated, e21=""))
            continue
        rows.append(_with_hash(_blank_row(
            session, "E21", ccy, "FRESH", _sign(number), False, snap, generated, "",
            bar, 1, 1, version, _public_valor("E21", _num_text(raw)),
        ), "usd_factor", as_of=as_of, generated_utc=generated, e21=_num_text(raw)))
    return rows


def load_factor(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_existing_keys(path):
    keys = set()
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return keys
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("fecha_cierre") and row.get("script") and row.get("ccy"):
                keys.add((row["fecha_cierre"], row["script"], row["ccy"]))
    return keys


def _ensure_session_type_column(raw):
    """Add session_type at the end when the header lacks it.

    Named-column readers keep every previous field. Existing data rows gain an
    empty session_type so history is not relabelled REGULAR. A file that
    already has the column is returned unchanged, byte for byte.
    """
    if not raw:
        return raw
    text = raw.decode("utf-8")
    if not text:
        return raw
    newline = "\r\n" if "\r\n" in text[:4096] else "\n"
    first, sep, rest = text.partition(newline if newline in text else "\n")
    if not sep:
        first = text
        rest = ""
        sep = ""
    try:
        fields = next(csv.reader([first]))
    except StopIteration:
        return raw
    if "session_type" in fields:
        return raw
    out = [first + ",session_type"]
    if rest:
        for line in rest.splitlines():
            out.append(line + "," if line else line)
    rebuilt = newline.join(out)
    if text.endswith(("\n", "\r\n")) and not rebuilt.endswith(("\n", "\r\n")):
        rebuilt += newline
    return rebuilt.encode("utf-8")


def _ensure_report_date_column(raw):
    """Add report_date at the end when the header lacks it.

    Existing rows gain an empty field. The value is the publication date of
    a weekly module and is never estimated here. A file that already has the
    column is returned unchanged, byte for byte.
    """
    if not raw:
        return raw
    text = raw.decode("utf-8")
    if not text:
        return raw
    newline = "\r\n" if "\r\n" in text[:4096] else "\n"
    first, sep, rest = text.partition(newline if newline in text else "\n")
    if not sep:
        first = text
        rest = ""
        sep = ""
    try:
        fields = next(csv.reader([first]))
    except StopIteration:
        return raw
    if "report_date" in fields:
        return raw
    out = [first + ",report_date"]
    if rest:
        for line in rest.splitlines():
            out.append(line + "," if line else line)
    rebuilt = newline.join(out)
    if text.endswith(("\n", "\r\n")) and not rebuilt.endswith(("\n", "\r\n")):
        rebuilt += newline
    return rebuilt.encode("utf-8")


def append_rows(path, rows, now=None, rejected_path=None, enforce_tplus1=False):
    """Append rows whose (fecha_cierre, script, ccy) is new.

    When the header already lists session_type and report_date, the previous
    bytes stay a prefix. When a column is missing it is added (empty on
    existing rows) and then the new rows are appended. report_date stays
    empty: this path does not estimate a publication date.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    raw = b""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            raw = fh.read()
    upgraded = _ensure_e7_columns(_ensure_report_date_column(_ensure_session_type_column(raw)))
    if upgraded != raw:
        with open(path, "wb") as fh:
            fh.write(upgraded)
        raw = upgraded
    have = read_existing_keys(path) if raw else set()
    written = _e7_now_z(now)
    if rejected_path is None:
        rejected_path = os.path.join(os.path.dirname(os.path.abspath(path)), "_ingest", "votes_rejected.jsonl")
    fresh = []
    rejected = 0
    for row in rows:
        key = (row["fecha_cierre"], row["script"], row["ccy"])
        if key in have:
            continue
        row = dict(row)
        if not row.get("written_at_utc"):
            row["written_at_utc"] = written
        if "legacy" not in row:
            row["legacy"] = ""
        if enforce_tplus1 and after_tplus1_close(row["fecha_cierre"], row["written_at_utc"], now=now):
            register_rejected(rejected_path, row, tplus1_close(row["fecha_cierre"]), row["written_at_utc"])
            rejected += 1
            sys.stderr.write(
                "e7 reject AFTER_TPLUS1_CLOSE %s %s %s deadline=%s\n"
                % (row["fecha_cierre"], row["script"], row["ccy"],
                   tplus1_close(row["fecha_cierre"]).strftime("%Y-%m-%dT%H:%M:%SZ"))
            )
            continue
        have.add(key)
        fresh.append(row)
    if not fresh:
        return 0
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    if not raw:
        writer.writeheader()
    for row in fresh:
        writer.writerow({col: row.get(col, "") for col in COLUMNS})
    extra = buf.getvalue()
    if not extra:
        return 0
    if raw and not raw.endswith(b"\n"):
        extra = "\n" + extra
    with open(path, "ab") as fh:
        fh.write(extra.encode("utf-8"))
    return len(fresh)


def rows_to_csv(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in COLUMNS})
    return buf.getvalue()


def weekly_report(rows, generated_utc):
    """A weekday is complete when all 13 modules and E21 have rows and none are MISSING or INGEST_INCOMPLETE.

    A CME/New York full close is not a session. It is omitted: not complete and
    not listed in incomplete_sessions, even if rows for that date are present.
    """
    by_day = {}
    for row in rows:
        by_day.setdefault(row["fecha_cierre"], []).append(row)
    weeks = {}
    complete_total = 0
    incomplete = []
    for day in sorted(by_day):
        if is_ny_full_close(day):
            continue
        got = {}
        bad = False
        for row in by_day[day]:
            # CRD_G8 es sombra: no entra en el cierre de los 13 módulos. Orden t235u.
            if row["script"] == "CRD_G8":
                continue
            got.setdefault(row["script"], set()).add(row["ccy"])
            if row["estado"] in ("MISSING", "INGEST_INCOMPLETE"):
                bad = True
        gate = [row for row in MODULES if row[0] != "CRD_G8"]
        scripts_ok = all(name in got and set(_ccys_for(name)) <= got[name] for name, *_ in gate)
        e21_ok = bool(got.get("E21"))
        # E21 has no fixed USD row. Complete means every currency that the file
        # emitted is present and at least one E21 row exists, with no MISSING.
        if scripts_ok and e21_ok and not bad:
            complete_total += 1
            year, week, _wd = date.fromisoformat(day).isocalendar()
            key = "%d-W%02d" % (year, week)
            bucket = weeks.setdefault(key, {"iso_week": key, "complete_days": 0, "sessions": []})
            bucket["complete_days"] += 1
            bucket["sessions"].append(day)
        else:
            incomplete.append(day)
    return {
        "rule_version": int(RULE_VERSION),
        "generated_utc": generated_utc,
        "definition": (
            "A weekday session is complete when every one of the 13 mail modules has a row "
            "for each of its currencies, E21 has at least one row, and no row of that session "
            "has estado MISSING or INGEST_INCOMPLETE. A CME/New York full close (no 1D candle) "
            "is not a session: it is not complete and it is not incomplete. Report only. "
            "§08 is not built."
        ),
        "complete_days_total": complete_total,
        "incomplete_sessions": incomplete,
        "weeks": [weeks[k] for k in sorted(weeks)],
    }


def write_weekly(path, rows, now_utc):
    report = weekly_report(rows, now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
        fh.write("\n")
    return report


def read_votes(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _http_status_text(failure):
    status = getattr(failure, "status", None)
    return status if status is not None else "n/a"


def _gmail_auth_suffix(failure):
    """`HTTP <status> gmail_reason=<r> gmail_status=<s>` for a 401/403 read.

    Absent for the token exchange and for every other Gmail read failure,
    including 5xx. Does not include the body or error.message.
    """
    status = getattr(failure, "status", None)
    if status not in (401, 403):
        return None
    reason = getattr(failure, "gmail_reason", None)
    gstatus = getattr(failure, "gmail_status", None)
    if reason is None and gstatus is None:
        return None
    return "HTTP %s gmail_reason=%s gmail_status=%s" % (
        status,
        reason if reason is not None else "none",
        gstatus if gstatus is not None else "none",
    )


def gmail_failure_log(session, attempt, failure):
    """Stderr line.

    The OAuth code is appended only for a token non-2xx. The Gmail reason and
    status are appended only for a Gmail API read that fails with 401 or 403.
    """
    line = "Gmail failure (%s) on attempt %s. Session %s." % (
        failure.kind, attempt, session.isoformat())
    code = getattr(failure, "oauth_error", None)
    if code:
        line += " HTTP %s oauth_error=%s" % (_http_status_text(failure), code)
    suffix = _gmail_auth_suffix(failure)
    if suffix:
        line += " " + suffix
    return line


def alert_text(session, attempt, exc):
    status = getattr(exc, "status", None)
    lines = [
        "votes ingest: Gmail failure",
        "session=%s" % session.isoformat(),
        "attempt=%s" % attempt,
        "error=%s" % type(exc).__name__,
        "kind=%s" % getattr(exc, "kind", "api"),
        "http_status=%s" % (status if status is not None else "n/a"),
    ]
    code = getattr(exc, "oauth_error", None)
    if code:
        shown = status if status is not None else "n/a"
        lines.append("HTTP %s oauth_error=%s" % (shown, code))
    suffix = _gmail_auth_suffix(exc)
    if suffix:
        lines.append(suffix)
    lines.extend([
        "MISSING rows are written only on the final attempt (05:20 WAT), after every page of the mailbox window through the 05:15 WAT cutoff has been listed.",
        "E21 from USD_FACTOR.json is written on both attempts.",
    ])
    return "\n".join(lines)


def urllib_http(method, url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp is not None else b""
        return exc.code, dict(exc.headers or {}), raw


def _jitter_unit():
    """Uniform [0, 1). Tests patch this, or pass `jitter_unit`, so backoff is deterministic."""
    return random.random()


def rate_limit_backoff_seconds(attempt_index, jitter_unit=None):
    """Wait before the next rate-limit try. Exponential, half-to-full jitter, per-sleep cap.

    `attempt_index` is 0 for the first wait. `jitter_unit` returns a float in
    [0, 1). The default is `_jitter_unit` (random).
    """
    unit = _jitter_unit() if jitter_unit is None else jitter_unit()
    if unit < 0.0:
        unit = 0.0
    elif unit > 1.0:
        unit = 1.0
    ceiling = RATE_LIMIT_BASE_S * (2 ** attempt_index)
    if ceiling > RATE_LIMIT_SLEEP_CAP_S:
        ceiling = RATE_LIMIT_SLEEP_CAP_S
    return ceiling * (0.5 + 0.5 * unit)


def _is_rate_limited(url, status, gmail_reason):
    """429, or a Gmail-read 403 whose whitelisted reason is a rate limit.

    Token-endpoint 401/403 stay auth, as before. The reason compared here is
    the whitelist output (`^[A-Za-z_]{1,40}$`), never the raw body.
    """
    if status == 429:
        return True
    if url == TOKEN_URL:
        return False
    return status == 403 and gmail_reason in _RATE_LIMIT_REASONS


def _request(http, method, url, headers, body, sleep, jitter_unit=None):
    last = GmailError("api")
    generic_i = 0
    rate_i = 0
    rate_waited = 0.0
    while generic_i < RETRIES:
        try:
            status, resp_headers, resp_body = http(method, url, headers, body)
        except Exception:
            last = GmailError("api")
            sleep(min(2 ** generic_i, 4))
            generic_i += 1
            continue
        oauth_error = None
        gmail_reason = None
        gmail_status = None
        if status < 200 or status >= 300:
            # Token exchange keeps oauth_error. A Gmail read keeps reason and
            # status only on 401 or 403 (orden Sander 14-nov-2026 16:08 WAT).
            if url == TOKEN_URL:
                oauth_error = oauth_error_code(resp_body)
            elif status in (401, 403):
                gmail_reason, gmail_status = gmail_api_error_fields(resp_body)
        if _is_rate_limited(url, status, gmail_reason):
            # Not auth. Do not attach gmail_reason: that suffix stays on auth
            # 401/403 only (orden 16:08 WAT). Orden 16:28 WAT.
            last = GmailError("api", status)
            if rate_i + 1 >= RATE_LIMIT_ATTEMPTS:
                raise last
            delay = rate_limit_backoff_seconds(rate_i, jitter_unit)
            remaining = RATE_LIMIT_TOTAL_WAIT_S - rate_waited
            if delay > remaining:
                delay = remaining
            if delay <= 0:
                raise last
            sleep(delay)
            rate_waited += delay
            rate_i += 1
            continue
        if status in (401, 403):
            raise GmailError("auth", status, oauth_error, gmail_reason, gmail_status)
        if status >= 500:
            last = GmailError("api", status, oauth_error, gmail_reason, gmail_status)
            sleep(min(2 ** generic_i, 4))
            generic_i += 1
            continue
        if status < 200 or status >= 300:
            raise GmailError("api", status, oauth_error, gmail_reason, gmail_status)
        return resp_body
    raise last


def _decode_b64(data):
    if not data:
        return ""
    pad = "=" * (-len(data) % 4)
    try:
        return base64_url(data + pad)
    except Exception:
        return ""


def base64_url(text):
    import base64
    return base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8", errors="replace")


def _body_from_payload(payload):
    if not isinstance(payload, dict):
        return ""
    data = (payload.get("body") or {}).get("data")
    if data and payload.get("mimeType", "text/plain") != "text/html":
        text = _decode_b64(data)
        if text:
            return text
    for part in payload.get("parts") or []:
        if part.get("mimeType") == "text/plain":
            text = _decode_b64((part.get("body") or {}).get("data"))
            if text:
                return text
    for part in payload.get("parts") or []:
        text = _body_from_payload(part)
        if text:
            return text
    return ""


def _header(payload, name):
    for item in (payload or {}).get("headers") or []:
        if item.get("name", "").lower() == name.lower():
            return item.get("value") or ""
    return ""


def list_message_ids(auth, query, http, sleep, jitter_unit=None):
    """Every id from every page of `query`. Stops only when nextPageToken is absent.

    Does not fetch message bodies. A later page is not optional: the caller
    writes MISSING only after this returns.
    """
    ids = []
    page = None
    pages = 0
    while True:
        url = LIST_URL + "?" + urllib.parse.urlencode({
            "q": query,
            "includeSpamTrash": "true",
            "maxResults": "100",
        })
        if page:
            url += "&pageToken=" + urllib.parse.quote(page)
        raw = _request(http, "GET", url, auth, None, sleep, jitter_unit)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise GmailError("api")
        if not isinstance(doc, dict):
            raise GmailError("api")
        pages += 1
        for item in doc.get("messages") or []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(item["id"])
        page = doc.get("nextPageToken")
        if not page:
            break
    return ids, pages


def fetch_messages(client_id, client_secret, refresh_token, http, sleep, session=None, jitter_unit=None, log_window=False):
    """Read-only Gmail list+get. Raises GmailError. Never modifies a message.

    `session` limits the search to that session's close→cutoff window. `run`
    always passes it. Without a session the base query is unchanged.
    `sleep` is the pause between messages.get and every retry wait — pass a
    no-op in tests. `jitter_unit` is the rate-limit jitter source in [0, 1).
    `log_window` prints the page count after every page has been listed and
    before any messages.get. The final pass sets it so MISSING is not written
    against a partial mailbox.
    """
    if not client_id or not client_secret or not refresh_token:
        raise GmailError("missing_secrets")
    form = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    try:
        raw = _request(
            http, "POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"},
            form, sleep, jitter_unit)
    except GmailError as exc:
        if exc.kind == "api" and exc.status in (400, 401):
            raise GmailError(
                "auth", exc.status, exc.oauth_error, exc.gmail_reason, exc.gmail_status)
        raise
    try:
        token = json.loads(raw.decode("utf-8")).get("access_token") or ""
    except (ValueError, AttributeError):
        token = ""
    if not token:
        raise GmailError("auth")
    auth = {"Authorization": "Bearer " + token}
    q = gmail_query_for_session(session) if session is not None else GMAIL_QUERY
    ids, pages = list_message_ids(auth, q, http, sleep, jitter_unit)
    if log_window:
        # After the last page, before any messages.get and before MISSING rows.
        print(
            "gmail window pages=%d ids=%d query=%s" % (pages, len(ids), q),
            file=sys.stderr,
        )
    out = []
    for index, mid in enumerate(ids):
        if index:
            sleep(MESSAGES_GET_PAUSE_S)
        raw = _request(
            http, "GET", LIST_URL + "/" + urllib.parse.quote(mid) + "?format=full",
            auth, None, sleep, jitter_unit)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise GmailError("api")
        payload = doc.get("payload") or {}
        internal = doc.get("internalDate")
        out.append({
            "id": doc.get("id") or mid,
            "date": parse_when(internal) or parse_when(_header(payload, "Date")),
            "subject": _header(payload, "Subject"),
            "body": _body_from_payload(payload) or doc.get("snippet") or "",
        })
    return out


def _send_alert(sender, session, attempt, exc):
    text = alert_text(session, attempt, exc)
    sender(text)
    return text


def default_sender(text):
    """One Telegram message via the existing notifier. No secret is printed."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import notify_telegram
    notify_telegram.send(text)


def build_rows(messages, session, write_missing, factor_doc, first_rtf10_v4_session=None):
    rows = rows_from_messages(
        messages, session, write_missing,
        first_rtf10_v4_session=first_rtf10_v4_session,
    )
    rows.extend(rows_from_factor(factor_doc, session))
    return rows


def gmail_failure_rows(session, write_missing, factor_doc, first_rtf10_v4_session=None):
    if not write_missing:
        rows = []
    elif _rtf10_v4_regime(session, first_rtf10_v4_session):
        names = [n for n, *_ in MODULES if n != "RTF10_G8"]
        rows = missing_rows(session, names=names)
        rows.extend(_rtf10_stale_rows(session))
    else:
        rows = missing_rows(session)
    rows.extend(rows_from_factor(factor_doc, session))
    return rows


def run(argv=None, http=None, sender=None, env=None, sleep=None, now=None, enforce_tplus1=False):
    parser = argparse.ArgumentParser(description="Append one session to data/votes.csv")
    parser.add_argument("--from-json", dest="from_json", help="local message JSON; no Gmail call")
    parser.add_argument("--session", help="YYYY-MM-DD session date (WAT). Default: previous weekday")
    parser.add_argument("--dry-run", action="store_true", help="print rows, write nothing, send nothing")
    parser.add_argument("--attempt", choices=("first", "final"),
                        help="first = 04:00 WAT (no MISSING); final = 05:20 WAT (write MISSING)")
    parser.add_argument("--factor", help="USD_FACTOR.json path")
    parser.add_argument("--votes", help="votes.csv path")
    parser.add_argument("--weekly", help="votes_weekly.json path")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    today = now.astimezone(WAT).date()
    if args.session:
        session = date.fromisoformat(args.session)
    else:
        session = previous_weekday(today)
    if session.weekday() >= 5:
        print("weekend session %s: no rows" % session.isoformat())
        return 0
    holiday = is_ny_full_close(session)
    attempt = args.attempt or attempt_for(session, now)
    write_missing = attempt == "final"
    factor_path = args.factor or os.path.join(ROOT, "data", "USD_FACTOR.json")
    votes_path = args.votes or os.path.join(ROOT, "data", "votes.csv")
    weekly_path = args.weekly or os.path.join(ROOT, "data", "votes_weekly.json")
    first_v4_hist = first_rtf10_v4_session_from_votes_path(votes_path)
    factor_doc = load_factor(factor_path)
    if factor_doc is None:
        print("USD_FACTOR.json unreadable at %s — E21 rows are not invented" % factor_path, file=sys.stderr)

    failure = None
    messages = []
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as fh:
            messages = json.load(fh)
        rows = build_rows(messages, session, write_missing, factor_doc,
                        first_rtf10_v4_session=resolve_first_rtf10_v4_session(
                            None, messages, session, history_date=first_v4_hist))
    else:
        http = http or urllib_http
        sleep = sleep or time.sleep
        try:
            messages = fetch_messages(
                (env.get("GMAIL_OAUTH_CLIENT_ID") or "").strip(),
                (env.get("GMAIL_OAUTH_CLIENT_SECRET") or "").strip(),
                (env.get("GMAIL_READONLY_REFRESH_TOKEN") or "").strip(),
                http, sleep, session=session, log_window=write_missing,
            )
        except GmailError as exc:
            failure = exc
            rows = gmail_failure_rows(session, write_missing, factor_doc,
                                    first_rtf10_v4_session=first_v4_hist)
        else:
            rows = build_rows(messages, session, write_missing, factor_doc,
                        first_rtf10_v4_session=resolve_first_rtf10_v4_session(
                            None, messages, session, history_date=first_v4_hist))

    # RELOJES-01 Anexo 1 A1. No mail on a full close: nothing, including no
    # MISSING on the final attempt. Mail that did arrive is stored normally.
    if holiday and not messages:
        print("holiday session %s: CLOSED" % session.isoformat())
        if failure is not None and not args.dry_run:
            _send_alert(sender or default_sender, session, attempt, failure)
            print(gmail_failure_log(session, attempt, failure), file=sys.stderr)
            return 1
        if failure is not None and args.dry_run:
            print(alert_text(session, attempt, failure), file=sys.stderr)
            return 1
        return 0
    for row in rows:
        row["session_type"] = "HOLIDAY" if holiday else row.get("session_type") or "REGULAR"

    if args.dry_run:
        sys.stdout.write(rows_to_csv(rows))
        if failure is not None:
            print(alert_text(session, attempt, failure), file=sys.stderr)
            return 1
        return 0

    if enforce_tplus1:
        kept = []
        written = _e7_now_z(now)
        rejected_path = os.path.join(os.path.dirname(os.path.abspath(votes_path)), "_ingest", "votes_rejected.jsonl")
        for row in rows:
            row = dict(row)
            if not row.get("written_at_utc"):
                row["written_at_utc"] = written
            if after_tplus1_close(row["fecha_cierre"], row["written_at_utc"], now=now):
                register_rejected(rejected_path, row, tplus1_close(row["fecha_cierre"]), row["written_at_utc"])
                sys.stderr.write(
                    "e7 reject AFTER_TPLUS1_CLOSE %s %s %s deadline=%s\n"
                    % (row["fecha_cierre"], row["script"], row["ccy"],
                       tplus1_close(row["fecha_cierre"]).strftime("%Y-%m-%dT%H:%M:%SZ"))
                )
                continue
            kept.append(row)
        rows = kept
    append_rows(votes_path, rows)
    write_weekly(weekly_path, read_votes(votes_path), now)
    if failure is not None:
        _send_alert(sender or default_sender, session, attempt, failure)
        print(gmail_failure_log(session, attempt, failure), file=sys.stderr)
        return 1
    print("session %s attempt %s rows_considered %s" % (session.isoformat(), attempt, len(rows)))
    return 0


def main(argv=None, **kwargs):
    return run(argv, **kwargs)


if __name__ == "__main__":
    # El CLI (workflow y ejecución manual del script) aplica el cierre t+1.
    # Las llamadas de librería, incluidos los tests ya existentes, pasan
    # enforce_tplus1=False salvo que lo pidan.
    sys.exit(main(enforce_tplus1=True))

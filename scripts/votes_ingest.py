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
         MISSING or INGEST_INCOMPLETE while the 05:00 WAT retry is still pending.
  final  05:00 WAT (04:00 UTC) — before the 05:15 WAT cutoff, anything still
         absent is MISSING. A Gmail failure (missing or invalid secrets, auth
         error, API error after retries) marks every module that has no row yet
         as MISSING (one row per module and currency, voto=NA) and sends one
         Telegram alert. When the token endpoint returns a non-2xx, the stderr
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
RULE_VERSION = "1"
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
# 05:15 WAT the morning after the session (04:15 UTC). This is the cutoff already
# named in this module's docstring, docs/VOTES_RULES_v1.md, and
# .github/workflows/votes_ingest.yml. attempt_for() is the 05:00 WAT retry,
# fifteen minutes earlier — not this cutoff. No new clock time.
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
    "RTF10_G8": "RTF10",
    "POL_G8": "POL",
    "IYDT_2Y": "IYDT",
    "IYDT_10Y": "IYDT",
    "FFVA_G8": "FFVA",
    "TOT_G8": "TOT",
    "EQF_G8": "EQF",
    "POS_G8": "POS",
    "E21": "R",
}

# name, version, parts, monolithic. Versions and part counts are the 12-nov-2026 close.
MODULES = (
    ("RISK_G8", "1.2.1", 1, True),
    ("XCCY_G8", "2.8.1", 2, False),
    ("XCCY_OIS_SHADOW", "1.0.0", 1, True),
    ("PSI_G8", "2.4.3", 3, False),
    ("CURVA_G8", "1.4.8", 5, False),
    ("RTF10_G8", "3.10.0", 5, False),
    ("POL_G8", "1.3.8", 4, False),
    ("IYDT_2Y", "1.7.2", 8, False),
    ("IYDT_10Y", "1.6.1", 8, False),
    ("FFVA_G8", "1.5.0", 5, False),
    ("TOT_G8", "1.1.3", 5, False),
    ("EQF_G8", "1.1.3", 5, False),
    ("POS_G8", "1.2.6", 6, False),
)
MODULE = {row[0]: {"version": row[1], "parts": row[2], "monolithic": row[3]} for row in MODULES}
MONOLITHIC = {name for name, spec in MODULE.items() if spec["monolithic"]}

# Bands seen in the 12-nov field list. No EXTREME band exists in this repo or in
# that field list, so an EXTREME (or any other) band is NA, not STRONG.
IYDT_BANDS = {
    "POS_STRONG": "+1",
    "NEG_STRONG": "-1",
    "POS_BELOW_STRONG": "0",
    "NEG_BELOW_STRONG": "0",
}
ALWAYS_NA = {
    "RISK_G8", "XCCY_G8", "XCCY_OIS_SHADOW", "CURVA_G8", "RTF10_G8",
    "TOT_G8", "EQF_G8", "POS_G8", "PSI_G8",
}
HAS_STATUS = {
    "XCCY_G8", "PSI_G8", "CURVA_G8", "RTF10_G8", "POL_G8",
    "IYDT_2Y", "IYDT_10Y", "FFVA_G8", "TOT_G8", "EQF_G8", "POS_G8",
}

COLUMNS = [
    "fecha_cierre", "script", "version", "ccy", "estado", "valor", "voto",
    "stale", "snapshot_id", "hora_recepcion_utc", "id_correo", "bar_close_ms",
    "family", "parts_expected", "parts_received", "rule_version",
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
    """final once the 05:00 WAT retry instant has been reached; otherwise first."""
    final_at = datetime(session.year, session.month, session.day, 4, 0, tzinfo=UTC) + timedelta(days=1)
    return "final" if now_utc >= final_at else "first"


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


def _estado_valor(name, ccy, kv):
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
    if name == "RTF10_G8":
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
    """→ (voto, stale). NA is never rewritten to 0."""
    stale = bool(bar_stale) or (status is not None and status != "FRESH")
    if name in ALWAYS_NA:
        return "NA", stale
    if name in ("IYDT_2Y", "IYDT_10Y"):
        if stale:
            return "NA", True
        return IYDT_BANDS.get(estado, "NA"), False
    if name == "FFVA_G8":
        roll = kv.get("%s_ROLL" % ccy, "")
        if stale or roll == "YES" or estado == "STALE":
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
        if stale or eligible != "YES":
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
               mail_ids, bar_ms, parts_expected, parts_received, version, valor=""):
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
    }


def missing_rows(session, names=None):
    """One MISSING row per module and its currencies. voto is NA. Nothing is filled with 0."""
    rows = []
    expected = expected_bar_close_ms(session)
    for name, _version, parts, _mono in MODULES:
        if names is not None and name not in names:
            continue
        for ccy in _ccys_for(name):
            rows.append(_blank_row(
                session, name, ccy, "MISSING", "NA", False, "", "", "",
                expected, parts, 0, MODULE[name]["version"],
            ))
    return rows


def _sent_ms(kv):
    raw = kv.get("SENT_MS", "")
    return int(raw) if str(raw).isdigit() else -1


def _part_no(kv):
    raw = kv.get("PART", "")
    return int(raw) if str(raw).isdigit() else None


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


def _incomplete(spec, parts_by_no, merged_parts_declared, batch_error, conflict, transport_bad):
    if transport_bad or batch_error or conflict:
        return True
    declared = spec["parts"]
    if merged_parts_declared is not None and merged_parts_declared != declared:
        return True
    wanted = set(range(1, declared + 1))
    return set(parts_by_no) != wanted


def _snapshot_incomplete_reason(name, part_items):
    spec = MODULE[name]
    if spec["monolithic"]:
        return False, part_items[-1][1], 1
    transport_bad = False
    batch_error = False
    declared = None
    by_no = {}
    ordered = []
    for _mail, kv in part_items:
        if kv.get("TRANSPORT") != "G8_PARTS_V1" or kv.get("TRANSPORT_REV") not in ("1.2", "1.3"):
            transport_bad = True
        if kv.get("BATCH_STATUS") == "ERROR":
            batch_error = True
        if str(kv.get("PARTS", "")).isdigit():
            n = int(kv["PARTS"])
            if declared is None:
                declared = n
            elif declared != n:
                transport_bad = True
        number = _part_no(kv)
        if number is None:
            transport_bad = True
        else:
            by_no[number] = kv
        ordered.append(kv)
    merged, conflict = _merge_parts(ordered)
    bad = _incomplete(spec, by_no, declared, batch_error, conflict, transport_bad)
    return bad, merged, len(by_no)


def rows_from_messages(messages, session, write_missing):
    """Bodies in, rows out. No network and no disk.

    `write_missing` is true only on the final attempt (05:00 WAT), which is
    before the 05:15 WAT cutoff. Until then, absent or incomplete modules are
    left unwritten so the retry can still complete them.
    """
    if isinstance(session, str):
        session = date.fromisoformat(session)
    expected = expected_bar_close_ms(session)
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
        item = ({"id": mail_id, "date": when}, kv)
        if name in MONOLITHIC:
            key = (name, bar_ms)
            prev = mono.get(key)
            if prev is None or _sent_ms(kv) >= _sent_ms(prev[1]):
                mono[key] = item
            continue
        dedup = kv.get("MESSAGE_ID") or "%s#%s" % (kv.get("SNAPSHOT_ID", ""), kv.get("PART", ""))
        snap = kv.get("SNAPSHOT_ID") or "%s:%s:%s" % (name, bar_ms, _sent_ms(kv))
        bucket = grouped.setdefault(name, {}).setdefault(bar_ms, {}).setdefault(snap, {})
        prev = bucket.get(dedup)
        if prev is None or _sent_ms(kv) >= _sent_ms(prev[1]):
            bucket[dedup] = item

    chosen = {}
    for (name, bar_ms), item in mono.items():
        chosen[name] = (bar_ms, [item], item[1].get("SNAPSHOT_ID") or "%s:%s" % (name, bar_ms))
    for name, bars in grouped.items():
        best = None
        for bar_ms, snaps in bars.items():
            for snap_id, deduped in snaps.items():
                items = list(deduped.values())
                sent = max(_sent_ms(kv) for _m, kv in items)
                rank = (sent, snap_id)
                if best is None or rank > best[0]:
                    best = (rank, bar_ms, items, snap_id)
        if best:
            chosen[name] = (best[1], best[2], best[3])

    rows = []
    present = set()
    for name, _version, parts, _mono in MODULES:
        if name not in chosen:
            continue
        present.add(name)
        bar_ms, items, snap_id = chosen[name]
        bad, merged, received = _snapshot_incomplete_reason(name, items)
        mail_ids = ";".join(sorted({m["id"] for m, _kv in items if m["id"]}))
        received_at = max((m["date"] for m, _kv in items if m["date"]), default="")
        version = merged.get("SCRIPT_VERSION") or MODULE[name]["version"]
        bar_stale = bar_ms != expected
        if bad:
            if not write_missing:
                present.discard(name)
                continue
            for ccy in _ccys_for(name):
                rows.append(_blank_row(
                    session, name, ccy, "INGEST_INCOMPLETE", "NA", bar_stale, snap_id,
                    received_at, mail_ids, bar_ms, parts, received, version,
                ))
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
            rows.append(_blank_row(
                session, name, ccy, estado, voto, stale, snap_id, received_at, mail_ids,
                bar_ms, parts, received if name not in MONOLITHIC else 1, version, valor,
            ))
    if write_missing:
        rows.extend(missing_rows(session, names=[n for n, *_ in MODULES if n not in present]))
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
            rows.append(_blank_row(
                session, "E21", ccy, "STALE" if raw is not None else "MISSING", "NA", True,
                snap, generated, "", bar, 1, 1, version, "" if raw is None else _num_text(raw),
            ))
            continue
        try:
            number = Decimal(str(raw))
        except InvalidOperation:
            rows.append(_blank_row(
                session, "E21", ccy, "STALE", "NA", True, snap, generated, "", bar, 1, 1, version, "",
            ))
            continue
        rows.append(_blank_row(
            session, "E21", ccy, "FRESH", _sign(number), False, snap, generated, "",
            bar, 1, 1, version, _num_text(raw),
        ))
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


def append_rows(path, rows):
    """Append rows whose (fecha_cierre, script, ccy) is new. Existing bytes stay a prefix."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    raw = b""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            raw = fh.read()
    have = read_existing_keys(path) if raw else set()
    fresh = []
    for row in rows:
        key = (row["fecha_cierre"], row["script"], row["ccy"])
        if key in have:
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
    """A weekday is complete when all 13 modules and E21 have rows and none are MISSING or INGEST_INCOMPLETE."""
    by_day = {}
    for row in rows:
        by_day.setdefault(row["fecha_cierre"], []).append(row)
    weeks = {}
    complete_total = 0
    for day in sorted(by_day):
        got = {}
        bad = False
        for row in by_day[day]:
            got.setdefault(row["script"], set()).add(row["ccy"])
            if row["estado"] in ("MISSING", "INGEST_INCOMPLETE"):
                bad = True
        scripts_ok = all(name in got and set(_ccys_for(name)) <= got[name] for name, *_ in MODULES)
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
    return {
        "rule_version": int(RULE_VERSION),
        "generated_utc": generated_utc,
        "definition": (
            "A weekday session is complete when every one of the 13 mail modules has a row "
            "for each of its currencies, E21 has at least one row, and no row of that session "
            "has estado MISSING or INGEST_INCOMPLETE. Report only. §08 is not built."
        ),
        "complete_days_total": complete_total,
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
        "MISSING rows are written only on the final attempt (05:00 WAT), before the 05:15 WAT cutoff.",
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


def fetch_messages(client_id, client_secret, refresh_token, http, sleep, session=None, jitter_unit=None):
    """Read-only Gmail list+get. Raises GmailError. Never modifies a message.

    `session` limits the search to that session's close→cutoff window. `run`
    always passes it. Without a session the base query is unchanged.
    `sleep` is the pause between messages.get and every retry wait — pass a
    no-op in tests. `jitter_unit` is the rate-limit jitter source in [0, 1).
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
    query = urllib.parse.urlencode({
        "q": q,
        "includeSpamTrash": "true",
        "maxResults": "100",
    })
    ids = []
    page = None
    while True:
        url = LIST_URL + "?" + query
        if page:
            url += "&pageToken=" + urllib.parse.quote(page)
        raw = _request(http, "GET", url, auth, None, sleep, jitter_unit)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise GmailError("api")
        for item in doc.get("messages") or []:
            if item.get("id"):
                ids.append(item["id"])
        page = doc.get("nextPageToken")
        if not page:
            break
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


def build_rows(messages, session, write_missing, factor_doc):
    rows = rows_from_messages(messages, session, write_missing)
    rows.extend(rows_from_factor(factor_doc, session))
    return rows


def gmail_failure_rows(session, write_missing, factor_doc):
    rows = missing_rows(session) if write_missing else []
    rows.extend(rows_from_factor(factor_doc, session))
    return rows


def run(argv=None, http=None, sender=None, env=None, sleep=None, now=None):
    parser = argparse.ArgumentParser(description="Append one session to data/votes.csv")
    parser.add_argument("--from-json", dest="from_json", help="local message JSON; no Gmail call")
    parser.add_argument("--session", help="YYYY-MM-DD session date (WAT). Default: previous weekday")
    parser.add_argument("--dry-run", action="store_true", help="print rows, write nothing, send nothing")
    parser.add_argument("--attempt", choices=("first", "final"),
                        help="first = 04:00 WAT (no MISSING); final = 05:00 WAT")
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
    attempt = args.attempt or attempt_for(session, now)
    write_missing = attempt == "final"
    factor_path = args.factor or os.path.join(ROOT, "data", "USD_FACTOR.json")
    votes_path = args.votes or os.path.join(ROOT, "data", "votes.csv")
    weekly_path = args.weekly or os.path.join(ROOT, "data", "votes_weekly.json")
    factor_doc = load_factor(factor_path)
    if factor_doc is None:
        print("USD_FACTOR.json unreadable at %s — E21 rows are not invented" % factor_path, file=sys.stderr)

    failure = None
    messages = []
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as fh:
            messages = json.load(fh)
        rows = build_rows(messages, session, write_missing, factor_doc)
    else:
        http = http or urllib_http
        sleep = sleep or time.sleep
        try:
            messages = fetch_messages(
                (env.get("GMAIL_OAUTH_CLIENT_ID") or "").strip(),
                (env.get("GMAIL_OAUTH_CLIENT_SECRET") or "").strip(),
                (env.get("GMAIL_READONLY_REFRESH_TOKEN") or "").strip(),
                http, sleep, session=session,
            )
        except GmailError as exc:
            failure = exc
            rows = gmail_failure_rows(session, write_missing, factor_doc)
        else:
            rows = build_rows(messages, session, write_missing, factor_doc)

    if args.dry_run:
        sys.stdout.write(rows_to_csv(rows))
        if failure is not None:
            print(alert_text(session, attempt, failure), file=sys.stderr)
            return 1
        return 0

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
    sys.exit(main())

#!/usr/bin/env python3
"""CFTC position date (Tuesday) and biweekly flag (t331u B4).

report_date of the collector is already the CFTC as-of / position date
(typically Tuesday). Publication is Friday 15:30 ET. The biweekly cadence
announced on 27-oct-2026 stays disabled until TFF FX files confirm two
distinct position dates in the same ISO week and the flag is turned on.

This module does not change windows, thresholds or existing COT fields.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

DEFAULT = {
    "enabled": False,
    "position_weekday": "Tuesday",
    "publication_weekday_expected": "Friday",
    "activate_when": "two distinct position dates in one ISO week confirmed in TFF FX files",
}


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()[:10]
    return date.fromisoformat(text)


def load_flag(path=None):
    """Read sources/cftc_cadence.json. Missing or invalid file → disabled."""
    cfg = dict(DEFAULT)
    if path is None:
        root = Path(__file__).resolve().parents[1]
        path = root / "sources" / "cftc_cadence.json"
    path = Path(path)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: data[k] for k in data if k in DEFAULT or k == "enabled"})
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    env = os.environ.get("CFTC_BIWEEKLY", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        cfg["enabled"] = True
    elif env in ("0", "false", "no", "off"):
        cfg["enabled"] = False
    cfg["enabled"] = bool(cfg.get("enabled"))
    return cfg


def is_tuesday(value):
    d = _as_date(value)
    return d is not None and d.weekday() == 1


def weeks_with_multiple_position_dates(dates):
    """ISO weeks that contain two or more distinct position dates (TFF FX confirmation)."""
    by = {}
    for raw in dates:
        d = _as_date(raw)
        if d is None:
            continue
        by.setdefault(d.isocalendar()[:2], set()).add(d)
    return sorted("%d-W%02d" % (y, w) for (y, w), days in by.items() if len(days) >= 2)


def tff_fx_confirms_biweekly(dates):
    return bool(weeks_with_multiple_position_dates(dates))


def should_activate(dates, cfg=None):
    """True only when the flag is on AND TFF FX files already show two dates in one week.

    Flag off: never activate, even if the files already show two dates.
    """
    cfg = cfg or load_flag()
    if not cfg.get("enabled"):
        return False
    return tff_fx_confirms_biweekly(dates)


def document_fields(report_date, cfg=None):
    """Additive top-level fields. Does not replace report_date."""
    cfg = cfg or load_flag()
    d = _as_date(report_date)
    return {
        "position_date": d.isoformat() if d else report_date,
        "position_weekday": d.strftime("%A") if d else None,
        "cadence": "biweekly" if cfg.get("enabled") else "weekly",
        "biweekly_enabled": bool(cfg.get("enabled")),
    }

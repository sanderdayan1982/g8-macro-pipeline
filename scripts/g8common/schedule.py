"""schedule — calendarios por jurisdicción, pasadas programadas y hora esperada de publicación (stdlib, ≥3.9).

Usado por la verificación de configuración (F1) y por el motor de frescura en paralelo (F3).
Toda hora de publicación se calcula en la zona IANA del proveedor, así que los cambios de hora
estacionales se aplican solos; los cron de GitHub son UTC fijos y no los siguen.
"""
import csv
import os
from datetime import date, datetime, time, timedelta, timezone

from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_calendars(root=ROOT):
    cal = {}
    with open(os.path.join(root, "sources", "calendars.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            cal.setdefault(r["calendar"], set()).add(r["date"])
    return cal


def is_bd(cals, name, d):
    if d.weekday() >= 5:
        return False
    for c in (name or "").split("|"):
        if c and d.isoformat() in cals.get(c, set()):
            return False
    return True


def add_bd(cals, name, d, n):
    step = 1 if n >= 0 else -1
    k = abs(int(n))
    while k:
        d += timedelta(days=step)
        if is_bd(cals, name, d):
            k -= 1
    return d


def load_passes(root=ROOT):
    with open(os.path.join(root, "sources", "schedules.csv"), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _cron_days(field):
    if field == "*":
        return set(range(7))
    out = set()
    for part in field.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return {(x % 7) for x in out}           # cron: 0/7 = domingo


def pass_times(p, start_utc, end_utc):
    """Instantes UTC de una pasada en [start, end]."""
    out = []
    day = start_utc.date() - timedelta(days=1)
    while day <= end_utc.date() + timedelta(days=1):
        if p.get("cron_utc"):
            m, h, _, _, dow = p["cron_utc"].split()
            cron_dow = (day.isoweekday() % 7)                 # lunes=1 … domingo=0
            if cron_dow in _cron_days(dow):
                t = datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=timezone.utc)
                if start_utc <= t <= end_utc:
                    out.append(t)
        elif p.get("local_time"):
            hh, mm = [int(x) for x in p["local_time"].split(":")]
            t = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(p["timezone"])).astimezone(timezone.utc)
            if start_utc <= t <= end_utc:
                out.append(t)
        day += timedelta(days=1)
    return out


def publication_utc(rule, cals, pub_day):
    """Instante UTC de publicación en el día de publicación dado (fecha local del proveedor)."""
    hh, mm = [int(x) for x in rule["pub_local"].split(":")]
    return datetime.combine(pub_day, time(hh, mm), tzinfo=ZoneInfo(rule["timezone"])).astimezone(timezone.utc)


def publication_days(rule, cals, start, end):
    """Días (locales) en que la fuente publica según su frecuencia, en [start, end]."""
    cal = rule.get("calendar") or ""
    freq = rule["frequency"]
    d, out = start, []
    while d <= end:
        if freq == "daily" and is_bd(cals, cal, d):
            out.append(d)
        elif freq.startswith("weekly:"):
            wd = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5}[freq.split(":")[1]]
            if d.weekday() == wd:
                out.append(d)
        elif freq == "monthly:BD1" and d.day <= 7 and is_bd(cals, cal, d):
            first = date(d.year, d.month, 1)
            while not is_bd(cals, cal, first):
                first += timedelta(days=1)
            if d == first:
                out.append(d)
        d += timedelta(days=1)
    return out


def coverage(rule, passes, cals, start, end, include_status=("ACTIVE", "PLANNED_F5", "PLANNED_INSTALL"), horizon_h=24):
    """Para cada publicación esperada: pasadas del grupo en (pub, pub+horizon]. → lista de dicts."""
    groups = set((rule.get("query_groups") or "").split("|")) - {""}
    ps = [p for p in passes if p["group"] in groups and p["status"] in include_status]
    res = []
    for day in publication_days(rule, cals, start, end):
        pub = publication_utc(rule, cals, day)
        hits = []
        for p in ps:
            for t in pass_times(p, pub, pub + timedelta(hours=horizon_h)):
                if t > pub:
                    hits.append((t, p["pass_id"], p["status"]))
        hits.sort()
        res.append({"pub_day": day, "pub_utc": pub, "passes": hits})
    return res

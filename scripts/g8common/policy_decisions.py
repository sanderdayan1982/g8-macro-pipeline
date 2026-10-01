"""
g8common/policy_decisions.py — Acta P-1 (2026-10-01)
====================================================
Decisiones de tipo oficial VERIFICADAS en la fuente primaria (comunicado del banco central),
para que los *_POLICY.csv no esperen al BIS, que publica WS_CBPOL con días o semanas de retraso.

Fichero: data/manual/policy_decisions.csv
    country,effective_date,rate,announced_date,source,verified
    JP,2026-09-24,1.25,2026-09-18,BoJ statement 2026-09-18,...
    AU,2026-09-30,4.60,2026-09-29,RBA media release 2026-09-29,...

Doctrina
  · El BIS manda en toda fecha que ya publica. Una decisión SOLO prolonga la serie por delante del último
    dato del BIS, desde su fecha efectiva, y solo hasta hoy (nunca fechas futuras).
  · Sin decisión posterior al último dato del BIS, no se rellena nada: el estado quo no se inventa.
  · Si el BIS ya cubre la fecha efectiva y su valor no coincide con la decisión → aviso de discrepancia
    (el BIS sigue mandando; la decisión queda como verificación cruzada).
  · Rejilla de fechas: lunes a viernes, como la serie diaria del BIS.
"""

import csv
import os
from datetime import date, datetime, timedelta

FILE = os.path.join("data", "manual", "policy_decisions.csv")
COUNTRIES = {"GB", "JP", "CH", "AU", "NZ"}
RATE_MIN, RATE_MAX = -1.0, 10.0


class DecisionError(ValueError):
    pass


def load(root):
    """→ list[dict(country, effective, rate, source)] ordenada. Fichero ausente → []. Fila inválida → error."""
    path = os.path.join(root, FILE)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        rows = [l for l in fh if l.strip() and not l.lstrip().startswith("#")]
    for i, r in enumerate(csv.DictReader(rows), start=2):
        c = (r.get("country") or "").strip().upper()
        if c not in COUNTRIES:
            raise DecisionError("fila %d: país %r no soportado" % (i, c))
        try:
            eff = datetime.strptime((r.get("effective_date") or "").strip(), "%Y-%m-%d").date()
            rate = float((r.get("rate") or "").strip())
        except ValueError:
            raise DecisionError("fila %d: fecha o tipo ilegible" % i)
        if not (RATE_MIN <= rate <= RATE_MAX):
            raise DecisionError("fila %d: tipo %.4f fuera de [%g, %g]" % (i, rate, RATE_MIN, RATE_MAX))
        if not (r.get("source") or "").strip():
            raise DecisionError("fila %d: falta la fuente primaria" % i)
        out.append({"country": c, "effective": eff, "rate": rate, "source": r["source"].strip()})
    out.sort(key=lambda d: (d["country"], d["effective"]))
    return out


def _d(s):
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def apply(rows, country, decisions, today):
    """rows: list[(yyyymmdd, float)] ordenada (del BIS o del CSV publicado).
    → (rows_extendidas, añadidas, avisos)."""
    mine = [d for d in decisions if d["country"] == country]
    warnings = []
    if not rows or not mine:
        return list(rows), 0, warnings
    last = _d(rows[-1][0])
    by_date = {k: v for k, v in rows}
    # verificación cruzada: decisiones que el BIS ya cubre
    for dec in mine:
        if dec["effective"] <= last:
            cover = [k for k in sorted(by_date) if _d(k) >= dec["effective"]]
            if cover and abs(by_date[cover[0]] - dec["rate"]) > 1e-9:
                warnings.append("discrepancia %s %s: BIS %.4f vs decisión %.4f (manda el BIS)"
                                % (country, cover[0], by_date[cover[0]], dec["rate"]))
    ahead = [d for d in mine if last < d["effective"] <= today]
    if not ahead:
        return list(rows), 0, warnings
    out, value, added = list(rows), rows[-1][1], 0
    d = last
    while d < today:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        for dec in ahead:
            if dec["effective"] <= d:
                value = dec["rate"]
        out.append((d.strftime("%Y%m%d"), value))
        added += 1
    return out, added, warnings


def read_published(path):
    """CSV OHLCV publicado → list[(yyyymmdd, close)]; ausente o ilegible → []."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for r in csv.DictReader(fh):
            k, v = (r.get("DATE") or "").strip(), (r.get("CLOSE") or "").strip()
            if len(k) == 8 and k.isdigit():
                try:
                    out.append((k, float(v)))
                except ValueError:
                    continue
    out.sort()
    return out

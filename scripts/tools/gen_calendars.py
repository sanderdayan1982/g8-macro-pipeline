#!/usr/bin/env python3
"""gen_calendars.py — genera sources/calendars.csv (festivos en día laborable por jurisdicción de publicación).

Herramienta de mantenimiento (no corre en CI). Requiere `pip install holidays` (librería, no fuente de datos).
Base: librería `holidays` + AJUSTES POR EVIDENCIA observada en los propios CSV del repo (ene–sep 2026),
cada uno documentado abajo. Todas las filas quedan PENDIENTE_CONTRASTE_OFICIAL hasta cotejarlas con el
calendario publicado por cada banco central; el motor de frescura nuevo (F3) corre en paralelo y no avisa
hasta la aceptación, así que un error aquí no genera alarmas.
Uso:  python3 scripts/tools/gen_calendars.py > sources/calendars.csv
"""
import csv
import datetime as dt
import sys

import holidays

Y = [2026, 2027]
V = holidays.__version__


def jp():
    h = dict(holidays.country_holidays("JP", years=Y))
    for y in Y:
        for m, d, n in ((12, 31, "大晦日 (cierre bancario)"), (1, 2, "銀行休業日"), (1, 3, "銀行休業日")):
            h.setdefault(dt.date(y, m, d), n)
    return h


def au():
    h = dict(holidays.country_holidays("AU", subdiv="NSW", years=Y))
    # evidencia: AONIA publicado el 2026-04-27 (ANZAC Day observado en NSW) → la RBA publicó; se excluye
    # el "observed" de ANZAC. Pendiente de contraste oficial.
    return {d: n for d, n in h.items() if "ANZAC Day (observed)" not in n}


def us_repo():
    h = dict(holidays.country_holidays("US", years=Y))
    # evidencia: SOFR sin publicación el 2026-04-03 (Viernes Santo) mientras FRED DGS sí tiene dato ese día.
    h[dt.date(2026, 4, 3)] = "Good Friday (sin SOFR observado en 2026)"
    return h


SPEC = [
    ("US", "country_holidays('US') — festivos federales (FRED/Treasury)", lambda: holidays.country_holidays("US", years=Y)),
    ("US_REPO", "country_holidays('US') + Viernes Santo observado — SOFR (NY Fed)", us_repo),
    ("TARGET", "financial_holidays('ECB') — TARGET2", lambda: holidays.financial_holidays("ECB", years=Y)),
    ("GB", "country_holidays('GB', subdiv='ENG') — bank holidays de Inglaterra", lambda: holidays.country_holidays("GB", subdiv="ENG", years=Y)),
    ("JP", "country_holidays('JP') + cierres bancarios 31-dic y 2/3-ene", jp),
    ("CA", "financial_holidays('TSX') ∪ country_holidays('CA') — proxy de días hábiles del BoC",
     lambda: holidays.financial_holidays("TSX", years=Y) | holidays.country_holidays("CA", years=Y)),
    ("AU", "country_holidays('AU', subdiv='NSW') sin ANZAC observado — RBA (evidencia 2026-04-27)", au),
    # evidencia: la tabla B2 tiene datos en los aniversarios de Wellington (19-ene) y Auckland (26-ene) → solo festivos nacionales
    ("NZ", "country_holidays('NZ') nacionales (sin aniversarios regionales, evidencia B2 ene-2026)", lambda: holidays.country_holidays("NZ", years=Y)),
    ("CH", "financial_holidays('SIX') — proxy del calendario suizo", lambda: holidays.financial_holidays("SIX", years=Y)),
]


def main():
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["calendar", "date", "name", "source", "verified"])
    for cal, src, fn in SPEC:
        h = dict(fn())
        for d in sorted(h):
            if d.weekday() < 5:
                w.writerow([cal, d.isoformat(), h[d], "holidays %s %s" % (V, src), "PENDIENTE_CONTRASTE_OFICIAL"])


if __name__ == "__main__":
    main()

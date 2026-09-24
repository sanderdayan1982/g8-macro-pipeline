#!/usr/bin/env python3
"""report_schedule.py — tabla de cobertura de consultas por regla (verano/invierno, activas vs previstas)."""
import csv
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from g8common import schedule as SC  # noqa: E402

ROOT = SC.ROOT
rules = list(csv.DictReader(open(os.path.join(ROOT, "sources", "freshness_rules.csv"), encoding="utf-8")))
passes = SC.load_passes(ROOT)
cals = SC.load_calendars(ROOT)
print("| Regla | Publicación esperada (UTC) jul / ene | 1.ª consulta (retraso) jul / ene | 2.ª consulta | Solo con pasadas activas hoy |")
print("|---|---|---|---|---|")
for r in rules:
    if not r["pub_local"] or not r["query_groups"]:
        continue
    cells = []
    for d0 in ((date(2027, 7, 1), date(2027, 7, 9)) if r["frequency"].startswith("monthly") else (date(2027, 7, 12), date(2027, 7, 18)),
               (date(2027, 1, 1), date(2027, 1, 9)) if r["frequency"].startswith("monthly") else (date(2027, 1, 11), date(2027, 1, 17))):
        c = SC.coverage(r, passes, cals, d0[0], d0[1])
        c = c[0] if c else None
        cells.append(c)
    act = SC.coverage(r, passes, cals, date(2027, 1, 11), date(2027, 1, 17), include_status=("ACTIVE",))
    n_act = min((len(x["passes"]) for x in act), default=0)

    def fmt(c, i):
        if not c or len(c["passes"]) <= i:
            return "—"
        t, pid, st = c["passes"][i]
        mins = int((t - c["pub_utc"]).total_seconds() // 60)
        return "%s %s (+%dh%02d)%s" % (pid, t.strftime("%H:%M"), mins // 60, mins % 60, "" if st == "ACTIVE" else "*")
    print("| %s | %s / %s | %s / %s | %s / %s | %s |" % (
        r["rule_id"], cells[0]["pub_utc"].strftime("%a %H:%M") if cells[0] else "—", cells[1]["pub_utc"].strftime("%a %H:%M") if cells[1] else "—",
        fmt(cells[0], 0), fmt(cells[1], 0), fmt(cells[0], 1), fmt(cells[1], 1),
        "sí (%d)" % n_act if n_act >= 2 else "**no** (%d) → depende de pasadas previstas" % n_act))
print("\n\\* = pasada prevista (PLANNED_F5 / PLANNED_INSTALL), aún no instalada.")

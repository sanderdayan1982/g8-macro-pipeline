"""B3 — nowcast diario del 2Y y el 5Y suizos, en SHADOW.

El cubo SNB `rendeiduebd` es una curva diaria, pero se publica una vez al mes.
El RSS oficial de tipos (17-oct-2026) trae R10, SARH y el tipo de política.
No trae 2Y ni 5Y. SIX publica el SARON, no esa curva. No hay otra fuente diaria
oficial en `fetch_chf_snb.py`.

Doctrina: diario aproximado antes que mensual congelado. Este módulo no reescribe
`CHF_SPOT_2Y.csv` ni `CHF_SPOT_5Y.csv`.

    w = plazo / 10
    mezcla(t) = (1 - w) * SARON(t) + w * NOM10(t)
    nowcast(t) = oficial(M) + mezcla(t) - mezcla(M)

M es el último cierre del cubo. SARON y NOM10 son el último impreso en o antes de t
(`CHF_SARON.csv`, `CHF_NOM_10Y.csv`). La fila M queda OFFICIAL. Los días hábiles
suizos posteriores quedan NOWCAST.
"""
import csv
import json
import math
import os
from datetime import date, timedelta

TENORS = (
    ("2Y", 2, "CHF_SPOT_2Y.csv", "CHF_SPOT_2Y_daily_shadow.csv"),
    ("5Y", 5, "CHF_SPOT_5Y.csv", "CHF_SPOT_5Y_daily_shadow.csv"),
)
LONG_YEARS = 10
SHADOW_JSON = "CHF_NOWCAST_shadow.json"
CALENDAR = "CH"


def weight(years, long_years=LONG_YEARS):
    return float(years) / float(long_years)


def blend(w_nom10, saron, nom10):
    return (1.0 - w_nom10) * saron + w_nom10 * nom10


def q(value):
    return float(f"{value:.6f}")


def asof(series, day):
    best = None
    for d, value in series:
        if d <= day:
            best = (d, value)
        else:
            break
    return best


def read_series(path):
    if not os.path.exists(path):
        return []
    out = {}
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rows = [line for line in fh if line.strip() and not line.startswith("#")]
    for row in csv.DictReader(rows):
        raw = (row.get("DATE") or row.get("Date") or "").strip()
        try:
            day = date.fromisoformat(raw[:10])
            value = float((row.get("Value") or row.get("VALUE") or "").strip())
        except ValueError:
            continue
        if math.isfinite(value):
            out[day] = value
    return sorted(out.items())


def bridge(official, saron, nom10, years, is_business_day):
    """Filas desde el ancla M. official/saron/nom10 son listas [(date, float)]."""
    if not official:
        return []
    anchor_day, anchor_value = official[-1]
    base_saron = asof(saron, anchor_day)
    base_nom = asof(nom10, anchor_day)
    if base_saron is None or base_nom is None:
        return []
    w = weight(years)
    base_blend = blend(w, base_saron[1], base_nom[1])
    last_anchor = max(saron[-1][0], nom10[-1][0])
    rows = [{
        "date": anchor_day,
        "value": q(anchor_value),
        "label": "OFFICIAL",
        "w_nom10": w,
        "saron": q(base_saron[1]),
        "saron_date": base_saron[0],
        "nom10": q(base_nom[1]),
        "nom10_date": base_nom[0],
    }]
    day = anchor_day + timedelta(days=1)
    while day <= last_anchor:
        if is_business_day(day):
            s = asof(saron, day)
            n = asof(nom10, day)
            if s is not None and n is not None:
                level = anchor_value + blend(w, s[1], n[1]) - base_blend
                rows.append({
                    "date": day,
                    "value": q(level),
                    "label": "NOWCAST",
                    "w_nom10": w,
                    "saron": q(s[1]),
                    "saron_date": s[0],
                    "nom10": q(n[1]),
                    "nom10_date": n[0],
                })
        day += timedelta(days=1)
    return rows


def _row_out(row):
    return {
        "date": row["date"].isoformat(),
        "value": row["value"],
        "label": row["label"],
        "w_nom10": row["w_nom10"],
        "saron": row["saron"],
        "saron_date": row["saron_date"].isoformat(),
        "nom10": row["nom10"],
        "nom10_date": row["nom10_date"].isoformat(),
    }


def build(root):
    import sys
    scripts = os.path.dirname(os.path.abspath(__file__))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from g8common import schedule as SC

    cals = SC.load_calendars(root)
    data = os.path.join(root, "data")
    saron = read_series(os.path.join(data, "CHF_SARON.csv"))
    nom10 = read_series(os.path.join(data, "CHF_NOM_10Y.csv"))
    tenors = {}
    for name, years, src, _out in TENORS:
        official = read_series(os.path.join(data, src))
        rows = bridge(official, saron, nom10, years, lambda day: SC.is_bd(cals, CALENDAR, day))
        tenors[name] = {
            "source": src,
            "years": years,
            "w_nom10": weight(years),
            "anchor_date": None if not rows else rows[0]["date"].isoformat(),
            "official": None if not rows else rows[0]["value"],
            "last_date": None if not rows else rows[-1]["date"].isoformat(),
            "last_value": None if not rows else rows[-1]["value"],
            "last_label": None if not rows else rows[-1]["label"],
            "rows": len(rows),
            "series": [_row_out(row) for row in rows],
        }
    return {
        "schema": "chf-nowcast-shadow-1",
        "mode": "SHADOW",
        "quality": "NOWCAST",
        "formula": "oficial(M) + mezcla(t) - mezcla(M); mezcla = (1-w)*SARON + w*NOM10; w = plazo/10",
        "anchors": {"short": "CHF_SARON.csv", "long": "CHF_NOM_10Y.csv"},
        "calendar": CALENDAR,
        "tenors": tenors,
    }


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = ["DATE,VALUE,LABEL,W_NOM10,SARON,SARON_DATE,NOM10,NOM10_DATE"]
    for row in rows:
        body.append(",".join([
            row["date"],
            f"{row['value']:.6f}",
            row["label"],
            f"{row['w_nom10']:.1f}",
            f"{row['saron']:.6f}",
            row["saron_date"],
            f"{row['nom10']:.6f}",
            row["nom10_date"],
        ]))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")
    os.replace(tmp, path)


def write_outputs(summary, root):
    data = os.path.join(root, "data")
    for name, _years, _src, out_name in TENORS:
        write_csv(os.path.join(data, out_name), summary["tenors"][name]["series"])
    path = os.path.join(data, SHADOW_JSON)
    slim = {
        "schema": summary["schema"],
        "mode": summary["mode"],
        "quality": summary["quality"],
        "formula": summary["formula"],
        "anchors": summary["anchors"],
        "calendar": summary["calendar"],
        "tenors": {
            name: {k: v for k, v in block.items() if k != "series"}
            for name, block in summary["tenors"].items()
        },
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(slim, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = build(root)
    write_outputs(out, root)
    for name, block in out["tenors"].items():
        print(f"[chf_nowcast_shadow] {name} {block['anchor_date']} {block['official']} → {block['last_date']} {block['last_value']} ({block['rows']} filas)")

#!/usr/bin/env python3
"""
aud_nowcast.py — Acta P-8 (2026-10-01): estimación diaria de los nominales AUD 2Y y 10Y (modelo EST_AUD_V1).

La RBA publica los rendimientos de los bonos (tabla F2) UNA vez por semana: viernes, con datos hasta el miércoles. Entre
publicaciones, AUD se quedaba congelado en §01, §01-b y §00. Este script estima cada día hábil posterior al último dato
de la RBA:

    nivel_estimado(d) = último dato RBA + Σ  β · Δdrivers   (días hábiles desde el dato RBA hasta d)

con β por MCO sin constante sobre variaciones diarias (últimos `calibration_years`), y drivers OFICIALES y públicos
(RBA F1 letra a 6 meses, diaria; Treasuries 2Y/10Y de la Fed del día hábil anterior en Nueva York). Toda la
especificación (objetivos, drivers, retardos, ventana) vive en sources/nowcast_aud.json — aquí no hay parámetros.

Salidas
  data/AUD_NOWCAST.csv   DATE,NOM2Y,NOM10,H_BD,ERR2Y_BP,ERR10_BP,BASE_DATE,MODEL — historial que solo crece: las filas
                         posteriores al último dato RBA se recalculan en cada ejecución; las anteriores quedan como registro
                         (permite medir el error real de cada estimación cuando llega la RBA).
  data/AUD_NOWCAST.json  estado, fecha base, betas, R², nº de observaciones, error esperado por horizonte, avisos.
Consumidores: docs/index.html (§01), scripts/s01b.py (§01-b, contexto), scripts/dashboard_alerts.py (§00).
rc 0 = estimación al día; 1 = algún insumo falta o va atrasado (se publica lo válido y el estado lo explica).
"""
import csv
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join("sources", "nowcast_aud.json")
OUT_CSV = os.path.join("data", "AUD_NOWCAST.csv")
OUT_JSON = os.path.join("data", "AUD_NOWCAST.json")
HEADER = ["DATE", "NOM2Y", "NOM10", "H_BD", "ERR2Y_BP", "ERR10_BP", "BASE_DATE", "MODEL"]
TARGETS = ("NOM2Y", "NOM10")


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10] if "-" in s else s[:8], fmt).date()
        except ValueError:
            continue
    return None


def read_series(path, col):
    """CSV del repo → lista ordenada [(date, float)]. Acepta DATE/Date, comentarios '#'. Fichero ausente → []."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        rows = [l for l in fh if l.strip() and not l.startswith("#")]
    out = []
    for r in csv.DictReader(rows):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        d = parse_date(r.get("DATE") or r.get("Date"))
        v = r.get(col)
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if d and math.isfinite(x):
            out.append((d, x))
    out.sort()
    dedup = {}
    for d, x in out:
        dedup[d] = x
    return sorted(dedup.items())


def asof(series, d, strict=False):
    """Último valor con fecha ≤ d (strict: < d). None si no hay."""
    lo, hi = 0, len(series)
    while lo < hi:
        mid = (lo + hi) // 2
        if series[mid][0] < d or (not strict and series[mid][0] == d):
            lo = mid + 1
        else:
            hi = mid
    return series[lo - 1][1] if lo else None


def weekdays_between(a, b):
    """Días hábiles (lunes-viernes) d con a < d ≤ b."""
    out, d = [], a + timedelta(days=1)
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def driver_value(series, lag_bd, d):
    return asof(series, d, strict=bool(lag_bd))


def calibrate(target, drivers, years, min_obs):
    """MCO sin constante sobre variaciones entre fechas consecutivas del objetivo. → dict o None."""
    if len(target) < 2:
        return None
    end = target[-1][0]
    start = end - timedelta(days=int(365.25 * years))
    pts = [(d, v) for d, v in target if d >= start]
    X, y = [], []
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        row = []
        for s, lag in drivers:
            a, b = driver_value(s, lag, d0), driver_value(s, lag, d1)
            if a is None or b is None:
                row = None
                break
            row.append(b - a)
        if row is not None:
            X.append(row)
            y.append(v1 - v0)
    if len(y) < min_obs:
        return None
    X, y = np.array(X), np.array(y)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    r2 = 1 - (resid @ resid) / (y @ y) if (y @ y) > 0 else 0.0
    err = {}
    for h in range(1, 11):                                     # error de la SUMA de h días (bandas por horizonte)
        if len(resid) > h:
            sums = np.convolve(resid, np.ones(h), mode="valid")
            err[h] = float(np.sqrt(np.mean(sums ** 2)) * 100)
    return {"beta": [float(b) for b in beta], "r2": float(r2), "n": int(len(y)), "err_bp": err,
            "window": [pts[0][0].isoformat(), end.isoformat()]}


def nowcast(target, drivers, fit, max_h):
    """Filas [(d, nivel, h, err_bp)] para los días hábiles posteriores al último dato del objetivo, hasta donde los
    drivers llegan. También devuelve la fecha hasta la que hay drivers (para el estado)."""
    base_d, base_v = target[-1]
    lag0 = [s for s, lag in drivers if not lag]
    reach = min((s[-1][0] for s in lag0), default=None) if lag0 else None
    if reach is None:
        return [], None
    rows, level, h = [], base_v, 0
    prev = base_d
    for d in weekdays_between(base_d, reach):
        dx = []
        for s, lag in drivers:
            a, b = driver_value(s, lag, prev), driver_value(s, lag, d)
            if a is None or b is None:
                return rows, reach
            dx.append(b - a)
        level += float(np.dot(fit["beta"], dx))
        h += 1
        if h > max_h:
            break
        rows.append((d, round(level, 4), h, round(fit["err_bp"].get(min(h, max(fit["err_bp"])), float("nan")), 1)))
        prev = d
    return rows, reach


def load_existing(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r["DATE"]: r for r in csv.DictReader(fh)}


def run(root=ROOT, today=None):
    today = today or datetime.now(timezone.utc).date()
    with open(os.path.join(root, CONFIG), encoding="utf-8") as fh:
        cfg = json.load(fh)
    data = os.path.join(root, "data")
    meta = {"model": cfg["model"], "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "config": CONFIG, "status": "OK", "warnings": [], "targets": {}}
    est = {}
    for t in TARGETS:
        tc = cfg["targets"][t]
        target = read_series(os.path.join(data, tc["file"]), tc["col"])
        drv = []
        for dc in cfg["drivers"][t]:
            s = read_series(os.path.join(data, dc["file"]), dc["col"])
            if not s:
                meta["warnings"].append("%s: driver %s ausente o ilegible (%s)" % (t, dc["name"], dc["file"]))
            drv.append((s, int(dc.get("lag_bd", 0))))
        tm = {"source": tc["source"], "base_date": target[-1][0].isoformat() if target else None,
              "base_value": target[-1][1] if target else None,
              "drivers": [{"name": dc["name"], "lag_bd": dc.get("lag_bd", 0), "last": (s[-1][0].isoformat() if s else None)}
                          for dc, (s, _) in zip(cfg["drivers"][t], drv)]}
        meta["targets"][t] = tm
        if not target or any(not s for s, _ in drv):
            meta["status"] = "INPUT_MISSING"
            continue
        fit = calibrate(target, drv, cfg["calibration_years"], cfg["min_obs"])
        if fit is None:
            meta["status"] = "CALIBRATION_FAILED"
            meta["warnings"].append("%s: menos de %d observaciones para calibrar" % (t, cfg["min_obs"]))
            continue
        tm.update({"beta": dict(zip([dc["name"] for dc in cfg["drivers"][t]], [round(b, 4) for b in fit["beta"]])),
                   "r2": round(fit["r2"], 3), "n_obs": fit["n"], "window": fit["window"],
                   "err_bp_by_h": {str(k): round(v, 1) for k, v in fit["err_bp"].items()}})
        rows, reach = nowcast(target, drv, fit, cfg["max_horizon_bd"])
        tm["drivers_reach"] = reach.isoformat() if reach else None
        tm["last_estimate"] = rows[-1][0].isoformat() if rows else None
        est[t] = {d: (v, h, e) for d, v, h, e in rows}
        # atraso de los insumos: los drivers diarios deberían llegar al día hábil anterior a hoy
        expected = [d for d in weekdays_between(today - timedelta(days=10), today - timedelta(days=1))][-1:]
        if reach and expected and reach < expected[0] - timedelta(days=3):
            meta["status"] = "INPUT_STALE"
            meta["warnings"].append("%s: los drivers diarios llegan solo al %s" % (t, reach.isoformat()))
    # ── CSV (historial que solo crece)
    old = load_existing(os.path.join(root, OUT_CSV))
    dates = sorted(set().union(*[set(v) for v in est.values()])) if est else []
    for d in dates:
        a, b = est.get("NOM2Y", {}).get(d), est.get("NOM10", {}).get(d)
        base = min(x for x in (meta["targets"]["NOM2Y"]["base_date"], meta["targets"]["NOM10"]["base_date"]) if x)
        old[d.strftime("%Y%m%d")] = {
            "DATE": d.strftime("%Y%m%d"), "NOM2Y": "" if not a else "%.4f" % a[0], "NOM10": "" if not b else "%.4f" % b[0],
            "H_BD": str((a or b)[1]), "ERR2Y_BP": "" if not a else "%.1f" % a[2], "ERR10_BP": "" if not b else "%.1f" % b[2],
            "BASE_DATE": base.replace("-", ""), "MODEL": cfg["model"]}
    tmp = os.path.join(root, OUT_CSV + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        w.writeheader()
        for k in sorted(old):
            w.writerow({h: old[k].get(h, "") for h in HEADER})
    os.replace(tmp, os.path.join(root, OUT_CSV))
    meta["rows_estimated_now"] = len(dates)
    tmp = os.path.join(root, OUT_JSON + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, os.path.join(root, OUT_JSON))
    return meta


def main():
    meta = run()
    for t, m in meta["targets"].items():
        print("%s: base RBA %s = %s · beta %s · R² %s · última estimación %s" % (
            t, m.get("base_date"), m.get("base_value"), m.get("beta"), m.get("r2"), m.get("last_estimate")))
    for w in meta["warnings"]:
        print("AVISO: " + w, file=sys.stderr)
    print("estado: %s · filas estimadas: %d" % (meta["status"], meta["rows_estimated_now"]))
    return 0 if meta["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())

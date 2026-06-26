#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_jpy_real.py
====================
ONE-SHOT: reconstruye el histórico de RY_G8_JPY.csv (NOM10/REAL10/BE10) recorriendo
el archivo diario del JSDA y aplicando el MISMO método on-the-run que fetch_jpy_real.py.

POR QUÉ 2018→hoy
----------------
El Excel de subastas del MoF avisa: "From October 2017, Lowest Accepted Price is NOT
multiplied by the indexation coefficient" — es decir, DESDE oct-2017 los precios del JGBi
son real-clean (per-100-face) y mi YTM-del-precio funciona directo. Antes de oct-2017 los
precios incluían el coeficiente de indexación y habría que dividir por él (no soportado aquí).
Por eso el backfill arranca en 2018-01 (todo post-convención, limpio), mismo span que EUR.

REUTILIZA fetch_jpy_real.py (parse_csv, compute, COUPONS, _open, _url_for, ...) → una sola
fuente de verdad para la tabla de cupones y el cálculo. Idempotente y RESUMIBLE: salta las
fechas ya presentes en el CSV (no las vuelve a bajar), así que se puede re-lanzar o cortar.

USO
---
  python3 backfill_jpy_real.py                       # 2018-01-02 -> hoy (días hábiles)
  START=2020-01-01 python3 backfill_jpy_real.py       # desde otra fecha
  END=2022-12-31  python3 backfill_jpy_real.py        # hasta otra fecha
  SAMPLE_EVERY=5  python3 backfill_jpy_real.py         # 1 de cada 5 días hábiles (más rápido)
  python3 backfill_jpy_real.py --redownload            # re-baja fechas ya presentes

Pensado para correr una vez vía GitHub Actions (workflow_dispatch) que comitea el CSV.
"""

import os
import sys
import csv
import argparse
import datetime as dt
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_jpy_real as fjr   # noqa: E402  (misma carpeta scripts/)

DEFAULT_START = dt.date(2018, 1, 2)     # post oct-2017 (precios real-clean)
CONVENTION_FLOOR = dt.date(2017, 10, 1)  # antes de esto los precios incluyen indexación
CHECKPOINT_EVERY = 100                   # guarda progreso cada N fechas escritas


def _parse_date(s, default):
    if not s:
        return default
    return dt.datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _load_existing(path):
    have = set()
    rows = {}
    if os.path.exists(path):
        with open(path, "r", newline="") as f:
            for parts in csv.reader(f):
                if not parts:
                    continue
                h = parts[0].strip()
                if h.startswith("#") or h == "DATE":
                    continue
                if len(parts) >= 4 and h.isdigit():
                    rows[h] = [p.strip() for p in parts[:4]]
                    have.add(h)
    return rows, have


def _flush(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(fjr.HEADER_COMMENT + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(fjr.COLS)
        for k in sorted(rows):
            w.writerow(rows[k])


def main():
    ap = argparse.ArgumentParser(description="Backfill histórico JPY real/BEI (JGBi on-the-run, JSDA)")
    ap.add_argument("--redownload", action="store_true",
                    help="re-procesa fechas ya presentes (por defecto se saltan)")
    args = ap.parse_args()

    start = _parse_date(os.environ.get("START"), DEFAULT_START)
    end = _parse_date(os.environ.get("END"), dt.date.today())
    step = int(os.environ.get("SAMPLE_EVERY", "1"))
    path = fjr.OUT_CSV

    if start < CONVENTION_FLOOR:
        sys.stderr.write("[backfill] AVISO: START %s es anterior a oct-2017. Los precios JGBi de "
                         "esa época incluyen el coeficiente de indexación y NO se manejan aquí; "
                         "los reales saldrían sesgados. Recorto START a %s.\n"
                         % (start.isoformat(), CONVENTION_FLOOR.isoformat()))
        start = CONVENTION_FLOOR

    rows, have = _load_existing(path)
    print("[backfill] %s -> %s  (step=%d) | %d filas ya presentes"
          % (start.isoformat(), end.isoformat(), step, len(have)))

    n_ok = n_skip_have = n_miss = n_err = n_new = 0
    bday_idx = -1
    d = start
    while d <= end:
        if d.weekday() < 5:                       # día hábil
            bday_idx += 1
            if step > 1 and (bday_idx % step) != 0:
                d += dt.timedelta(days=1); continue
            ds = d.strftime("%Y%m%d")
            if ds in have and not args.redownload:
                n_skip_have += 1
                d += dt.timedelta(days=1); continue
            url = fjr._url_for(d)
            try:
                with fjr._open(url) as r:
                    if getattr(r, "status", 200) != 200:
                        n_miss += 1; d += dt.timedelta(days=1); continue
                    data = r.read()
                if not data or len(data) < 1000:
                    n_miss += 1; d += dt.timedelta(days=1); continue
                text = data.decode(fjr.ENCODING, "replace")
                fd, jgbi, noms = fjr.parse_csv(text)
                res = fjr.compute(fd, jgbi, noms)
                key = res["date"].strftime("%Y%m%d")
                rows[key] = [key, "%g" % res["NOM10"], "%g" % res["REAL10"], "%g" % res["BE10"]]
                if key not in have:
                    n_new += 1
                have.add(key)
                n_ok += 1
                if n_ok % 25 == 0:
                    print("  ... %s  OTR#%d  NOM %.3f REAL %.3f BE %.3f  [ok=%d miss=%d]"
                          % (key, res["otr_num"], res["NOM10"], res["REAL10"], res["BE10"],
                             n_ok, n_miss))
                if n_new and (n_new % CHECKPOINT_EVERY == 0):
                    _flush(rows, path)
                    print("  [checkpoint] %d filas guardadas" % len(rows))
            except urllib.error.HTTPError:
                n_miss += 1                       # típicamente festivo japonés (no hay fichero)
            except Exception as e:
                n_err += 1
                sys.stderr.write("  [err] %s: %s\n" % (ds, e))
        d += dt.timedelta(days=1)

    _flush(rows, path)
    print("\n[backfill] HECHO. ok=%d nuevas=%d ya-tenía=%d sin-fichero/festivo=%d errores=%d"
          % (n_ok, n_new, n_skip_have, n_miss, n_err))
    print("[backfill] %s tiene ahora %d filas (%s .. %s)"
          % (path, len(rows), min(rows) if rows else "-", max(rows) if rows else "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

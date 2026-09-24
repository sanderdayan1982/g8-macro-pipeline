#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_eur_real.py
====================
ONE-SHOT: reconstruye el HISTORICO diario de RY_G8_EUR.csv (NOM10/REAL10/BE10)
recorriendo todos los XLSX mensuales del Bundesbank hacia atras hasta donde el
dato aguanta.

NO es parte del pipeline nightly. Se corre UNA vez (desde tu Mac o un job manual)
para llenar el histórico que al scraper diario le costaría meses acumular. Reutiliza
el motor ya validado de fetch_eur_real.py (parseo, interpolación, upsert idempotente):
no duplica lógica.

QUE HACE
--------
1. Recorre el listado paginado del Bundesbank (pageNumString=0..N) y recoge SOLO
   los enlaces que casan el patrón XLSX moderno  …/{YYYY-MM}-excel-data.xlsx.
   Ignora automáticamente los PDF antiguos y el patrón viejo (current-prices-…),
   así que el formato decide el suelo, no una fecha inventada.
2. Por cada XLSX mensual: descarga, itera TODAS las hojas (días hábiles) y calcula
   NOM10/REAL10/BE10 por día — con el GUARDARRAIL de bracket: solo emite la fila si
   hay ≥2 linkers que enmarcan 10Y (±1.5y). Antes de ~2009 hay 0-1 linkers alemanes,
   así que esos días se saltan (el real sería extrapolación basura).
3. Upsert idempotente sobre el MISMO RY_G8_EUR.csv que escribe el diario (se fusiona;
   last-write-wins por fecha). Sin freshness gate (es histórico).

USO
---
  python3 backfill_eur_real.py                  # producción: descubre, descarga TODO, escribe
  python3 backfill_eur_real.py --dry-run        # descubre+descarga+parsea, resumen, NO escribe
  python3 backfill_eur_real.py --test FILE.xlsx # valida multi-hoja contra un XLSX local (no red)
  python3 backfill_eur_real.py --max-pages 45 --sleep 0.3 --out data/RY_G8_EUR.csv

Stdlib pura salvo openpyxl (vía fetch_eur_real). Sin claves, sin auth. Fallback SSL
heredado (red Bata). Politeness sleep entre descargas.
"""

import sys
import io
import time
import argparse
import datetime as dt

import fetch_eur_real as fx   # motor compartido (mismo directorio scripts/)

# Listado paginado: la URL de paginacion que devuelve resultados (probada).
# Sin query=*/sort (el endpoint ya ordena por "Latest" y pagina sobre TODO el set).
BBKSEARCH = "https://www.bundesbank.de/action/en/810710/bbksearch?pageNumString=%d"
DEFAULT_MAX_PAGES = 45        # el archivo real son ~39 paginas; margen de sobra
STOP_AFTER_EMPTY = 2          # paginas consecutivas SIN XLSX -> hemos pasado al tail PDF


# --------------------------------------------------------------------------- #
# 1) DESCUBRIMIENTO de todos los XLSX mensuales (walk paginado)
# --------------------------------------------------------------------------- #
def discover_all_monthly_xlsx(max_pages=DEFAULT_MAX_PAGES, sleep=0.3):
    seen = {}            # (year, month) -> url
    empty_streak = 0
    for pg in range(0, max_pages):
        try:
            html = fx.http_text(BBKSEARCH % pg)
        except Exception as e:
            sys.stderr.write("[discover] pagina %d fallo (%s); sigo\n" % (pg, e))
            continue
        hits = 0
        for m in fx.XLSX_RE.finditer(html):
            ym = (int(m.group(1)), int(m.group(2)))
            if ym not in seen:
                seen[ym] = fx.BASE + m.group(0)
                hits += 1
        sys.stderr.write("[discover] pagina %2d -> %d XLSX nuevos (acum %d)\n" % (pg, hits, len(seen)))
        if hits == 0 and pg == 0:
            # diagnostico inmediato si la PRIMERA pagina no da nada
            if "excel-data" in html:
                sys.stderr.write("[discover] OJO: la pagina contiene 'excel-data' pero el regex no casa.\n")
            elif "resource/blob" in html:
                sys.stderr.write("[discover] OJO: hay 'resource/blob' pero no '-excel-data.xlsx' en pagina 0.\n")
            else:
                sys.stderr.write("[discover] OJO: pagina 0 sin 'excel-data' ni 'resource/blob' (len=%d). "
                                 "Contenido distinto al esperado.\n" % len(html))
        if hits == 0:
            empty_streak += 1
            if empty_streak >= STOP_AFTER_EMPTY:
                sys.stderr.write("[discover] %d paginas seguidas sin XLSX -> fin del tramo XLSX\n"
                                 % STOP_AFTER_EMPTY)
                break
        else:
            empty_streak = 0
        time.sleep(sleep)
    return seen


# --------------------------------------------------------------------------- #
# 2) DESCARGA + PARSEO de cada mes -> acumula results por día
# --------------------------------------------------------------------------- #
def harvest(urls_by_ym, sleep=0.3):
    all_rows = []
    months = sorted(urls_by_ym.keys(), reverse=True)   # de reciente a antiguo
    n = len(months)
    fully_empty = []     # meses que no aportaron NINGUNA fila (era pre-bracket)
    for i, ym in enumerate(months, 1):
        url = urls_by_ym[ym]
        tag = "%04d-%02d" % ym
        try:
            data = fx.http_bytes(url)
            rows = fx.parse_all_sheets(io.BytesIO(data), require_bracket=True)
        except Exception as e:
            sys.stderr.write("[harvest] %s FALLO (%s); sigo\n" % (tag, e))
            continue
        if rows:
            all_rows.extend(rows)
        else:
            fully_empty.append(tag)
        sys.stderr.write("[harvest] %3d/%3d  %s  -> %2d dias  (acum %d)\n"
                         % (i, n, tag, len(rows), len(all_rows)))
        time.sleep(sleep)
    return all_rows, fully_empty


# --------------------------------------------------------------------------- #
# RESUMEN
# --------------------------------------------------------------------------- #
def _summary(rows, fully_empty, wrote=None):
    if not rows:
        print("Sin filas. Nada que escribir.")
        return
    rows = sorted(rows, key=lambda r: r["date"])
    d0, d1 = rows[0]["date"], rows[-1]["date"]
    print("\n================= RESUMEN BACKFILL =================")
    print("Filas (dias habiles con REAL10 fiable): %d" % len(rows))
    print("Rango histórico reconstruido          : %s  ->  %s" % (d0.isoformat(), d1.isoformat()))
    print("Suelo (primer día con bracket 10Y)    : %s" % d0.isoformat())
    if fully_empty:
        print("Meses 100%% saltados (era pre-bracket) : %d  (%s ... %s)"
              % (len(fully_empty), fully_empty[-1], fully_empty[0]))
    print("Muestra (primeros y últimos):")
    for r in rows[:2] + rows[-2:]:
        print("   %s  NOM10 %6.3f  REAL10 %6.3f  BE10 %6.3f"
              % (r["date"].isoformat(), r["NOM10"], r["REAL10"], r["BE10"]))
    if wrote is not None:
        nuevas, total = wrote
        print("Upsert -> %d filas aportadas | %d filas totales en el CSV" % (nuevas, total))
    print("===================================================")


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Backfill histórico de RY_G8_EUR.csv (Bundesbank linkers)")
    ap.add_argument("--test", metavar="XLSX", help="valida multi-hoja contra un XLSX local (no red, no escribe)")
    ap.add_argument("--dry-run", action="store_true", help="descubre+descarga+parsea, resumen, NO escribe")
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="paginas máximas a recorrer")
    ap.add_argument("--sleep", type=float, default=0.3, help="pausa (s) entre requests (politeness)")
    ap.add_argument("--out", default=fx.OUT_CSV, help="ruta del CSV de salida")
    args = ap.parse_args()

    # Modo test: un fichero local, multi-hoja, sin red, sin escribir.
    if args.test:
        rows = fx.parse_all_sheets(args.test, require_bracket=True)
        _summary(rows, [])
        print("\n[--test] OK (no se escribió CSV).")
        return 0

    # Descubrimiento + cosecha
    sys.stderr.write("[backfill] descubriendo XLSX mensuales...\n")
    urls = discover_all_monthly_xlsx(max_pages=args.max_pages, sleep=args.sleep)
    if not urls:
        sys.stderr.write("ERROR: no encontré ningún XLSX mensual. ¿Cambió el listado?\n")
        return 1
    sys.stderr.write("[backfill] %d meses XLSX. Descargando y parseando...\n" % len(urls))
    rows, fully_empty = harvest(urls, sleep=args.sleep)

    if args.dry_run:
        _summary(rows, fully_empty)
        print("\n[--dry-run] OK (no se escribió CSV).")
        return 0

    wrote = fx.write_rows(rows, args.out)
    _summary(rows, fully_empty, wrote=wrote)
    print("\nEscrito -> %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

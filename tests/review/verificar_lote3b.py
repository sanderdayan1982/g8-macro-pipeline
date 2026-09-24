"""Verificación del lote 3B: ejecuta sus pruebas de aceptación y resume el inventario de descargadores.
Solo directorios temporales y transportes simulados (sin red, sin credenciales). Salida JSON; código 0 si todo pasa.

    python tests/review/verificar_lote3b.py [raíz_del_repo]
"""
import collections
import csv
import io
import json
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2])
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests"), str(ROOT / "scripts" / "tools")]
os.environ["G8_NO_SEND"] = "1"

SUITES = {
    "bills_acm_suelos (equivalencia + fallos)": "test_lote3b_fetchers",
    "reales EUR/JPY + backfills": "test_lote3b_reales",
    "descargadores del Mac": "test_lote3b_mac",
    "inventario sin omisiones": "test_inventory",
}
out = {"suites": {}, "inventario": {}}
ok = True
saved = os.dup(1)
os.dup2(2, 1)                                   # la salida de las pruebas no se mezcla con el JSON
try:
    for label, mod in SUITES.items():
        r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
            unittest.defaultTestLoader.loadTestsFromName(mod))
        out["suites"][label] = {"pruebas": r.testsRun, "fallos": len(r.failures) + len(r.errors),
                                "omitidas": len(r.skipped),
                                "detalle": [str(t) for t, _ in r.failures + r.errors][:10]}
        ok = ok and r.wasSuccessful()
finally:
    sys.stdout.flush()
    os.dup2(saved, 1)
with open(ROOT / "sources" / "ingest_inventory.csv", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
by = collections.OrderedDict()
for r in rows:
    by.setdefault(r["estado"], []).append(r["script"])
out["inventario"] = by
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if ok else 1)

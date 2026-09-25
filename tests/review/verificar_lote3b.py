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
# Hallazgos de las revisiones de 4c4847c (B3-*) y 1e31fd1 (B3R1-1): pruebas de aceptación que fallan en la versión
# revisada y pasan con la corrección
B3 = {
    "B3-1 reales: REAL10/BE10 corregidos con NOM10 igual": ("test_lote3b_reales", "B3_1"),
    "B3-2 Mac: Retry-After/503/redirección no eludidos por otro perfil": ("test_lote3b_mac", "b3_2"),
    "B3-3 Mac: presupuesto único para toda la cadena de transportes": ("test_lote3b_mac", "b3_3"),
    "B3-2/3 Mac: extremo a extremo TONA con bibliotecas HTTP dobles": ("test_lote3b_mac", "end_to_end"),
    "B3-4 Mac: max_date del registro de escritura": ("test_lote3b_mac", "WriteMetadata"),
    "B3R1-1 Mac: plazo total efectivo con requests real y biblioteca estándar (servidor lento local)":
        ("test_lote3b_mac", "RequestsDeadlineReal"),
}


def _filtered(mod, key):
    suite = unittest.TestSuite()
    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            elif key in t.id():
                suite.addTest(t)
    walk(unittest.defaultTestLoader.loadTestsFromName(mod))
    return suite


out = {"hallazgos_B3": {}, "suites": {}, "inventario": {}}
ok = True
saved = os.dup(1)
os.dup2(2, 1)                                   # la salida de las pruebas no se mezcla con el JSON
try:
    for label, (mod, key) in B3.items():
        suite = _filtered(mod, key)
        r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        out["hallazgos_B3"][label] = {"pruebas": r.testsRun, "fallos": len(r.failures) + len(r.errors),
                                      "detalle": [str(t) for t, _ in r.failures + r.errors][:10]}
        ok = ok and r.wasSuccessful() and r.testsRun > 0
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

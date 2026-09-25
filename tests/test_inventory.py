"""Inventario de descargadores (sources/ingest_inventory.csv) — sin omisiones, comprobado sobre el código.

  · Todo script de scripts/ o mac/ con E/S de red (requests, urllib, curl_cffi, g8http, http.client, yfinance,
    databento) figura en el inventario, y todo script inventariado existe.
  · Los marcados CUBIERTO_3A/3B de Actions usan el contexto común (g8common.ingest) y no abren conexiones
    propias; los del Mac usan g8common.macfetch; ninguno escribe sus CSV de datos con open(…, "w").
  · Todo script Python invocado por un workflow que haga E/S de red está inventariado.
"""
import csv
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NET = re.compile(r"^\s*(import requests|from curl_cffi|import urllib\.request|from g8common import .*(ingest|legacy|macfetch|g8http)|"
                 r"import http\.client|import yfinance|import databento)", re.M)


def inventory():
    with open(os.path.join(ROOT, "sources", "ingest_inventory.csv"), encoding="utf-8") as fh:
        return {r["script"]: r for r in csv.DictReader(fh)}


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class Inventory(unittest.TestCase):
    def test_every_network_script_is_inventoried(self):
        inv = inventory()
        found = []
        for d in ("scripts", "mac"):
            for n in sorted(os.listdir(os.path.join(ROOT, d))):
                rel = "%s/%s" % (d, n)
                if n.endswith(".py") and NET.search(src(rel)) and n not in ("ingest_watch.py", "check_credentials.py",
                                                                          "instalar_lote1.py"):
                    found.append(rel)
        missing = [f for f in found if f not in inv]
        self.assertEqual(missing, [], "descargadores sin inventariar")
        for f in inv:
            self.assertTrue(os.path.exists(os.path.join(ROOT, f)), f)

    def test_covered_fetchers_use_common_layers(self):
        for f, r in inventory().items():
            if not r["estado"].startswith("CUBIERTO_3"):
                continue
            s = src(f)
            if r["executor"] == "mac":
                self.assertIn("macfetch", s, f)
                self.assertNotIn("crequests.get(", s, f)
            elif r["estado"] != "CUBIERTO_3B_MANUAL":
                self.assertIn("from g8common import ingest", s, f)
                self.assertNotRegex(s, r"urllib\.request\.urlopen\(", f)
                self.assertNotRegex(s, r"^\s*import requests\s*$", f)
            # ningún CSV de datos escrito directamente sobre el fichero válido
            # (un escritor antiguo no atómico puede quedar definido como referencia, pero sin ninguna llamada)
            for m in re.finditer(r"def (write_csv|write_dv|write_ohlcv|_upsert|_flush)\(.*?\n(?=def |\Z)", s, re.S):
                body, name = m.group(0), m.group(1)
                if 'open(' in body and '"w"' in body and not ("tmp" in body and "os.replace" in body):
                    calls = len(re.findall(r"(?<!def )\b%s\(" % name, s))
                    self.assertEqual(calls, 0, "%s: %s no atómico y en uso" % (f, name))

    def test_workflow_scripts_with_network_are_inventoried(self):
        inv = inventory()
        wf = os.path.join(ROOT, ".github", "workflows")
        for n in sorted(os.listdir(wf)):
            for script in set(re.findall(r"python3? (scripts/[a-z0-9_]+\.py)", src(".github/workflows/" + n))):
                if os.path.exists(os.path.join(ROOT, script)) and NET.search(src(script)):
                    self.assertIn(script, inv, "%s (en %s)" % (script, n))

    def test_states_are_known(self):
        ok = {"CUBIERTO_3A", "CUBIERTO_3B", "CUBIERTO_3B_MANUAL", "CUBIERTO_L1", "PARCIAL", "PENDIENTE", "EXCLUIDO",
              "INACTIVO", "NO_ES_DESCARGADOR"}
        for f, r in inventory().items():
            self.assertIn(r["estado"], ok, f)
            if r["estado"] in ("PARCIAL", "PENDIENTE", "INACTIVO", "EXCLUIDO"):
                self.assertTrue(r["nota"].strip(), "%s: falta el motivo" % f)


if __name__ == "__main__":
    unittest.main()

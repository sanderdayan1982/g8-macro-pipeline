"""t336u: una publicación VPS no puede quitar Status=PROXY a una serie que ya lo tenía.

Regresión del incidente 559234b8 (filas 13–20 nov de NZD_CASH_ON perdieron la marca;
restore be4ef608; arreglo de código en #110 / macfetch.write). Test nuevo; no se
editan tests existentes. t336u, orden expresa Sander 22-nov.
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# Recorte del incidente: valores iguales, pero la publicación mala pierde Status/Source/ProxyDate.
PREV = (
    "Date,Value,Status,Source,ProxyDate\n"
    "2026-11-12,2.8,,,\n"
    "2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13\n"
    "2026-11-16,2.75,PROXY,NZD_OCR,2026-11-13\n"
    "2026-11-17,2.75,PROXY,NZD_OCR,2026-11-13\n"
    "2026-11-18,2.75,PROXY,NZD_OCR,2026-11-13\n"
    "2026-11-19,2.75,PROXY,NZD_OCR,2026-11-13\n"
    "2026-11-20,2.75,PROXY,NZD_OCR,2026-11-13\n"
)
# Como 559234b8: mismas fechas y valores, sin marca PROXY.
DROPPED = (
    "Date,Value\n"
    "2026-11-12,2.8\n"
    "2026-11-13,2.75\n"
    "2026-11-16,2.75\n"
    "2026-11-17,2.75\n"
    "2026-11-18,2.75\n"
    "2026-11-19,2.75\n"
    "2026-11-20,2.75\n"
)
# Recuperación oficial: B2 vuelve a imprimir el overnight del 20-nov; el resto sigue PROXY.
OFFICIAL_RECOVERY = (
    "Date,Value\n"
    "2026-11-12,2.8\n"
    "2026-11-20,2.81\n"
)


def _status_by_date(text: str) -> dict[str, str]:
    rows = list(csv.DictReader(io.StringIO(text)))
    out = {}
    for r in rows:
        d = (r.get("Date") or "").strip()
        if d:
            out[d] = (r.get("Status") or "").strip()
    return out


def dropped_proxy_marks(previous: str, published: str, fetched_official: str | None = None) -> set[str]:
    """Fechas que tenían PROXY en `previous` y ya no lo tienen en `published`.

    Si `fetched_official` trae esa fecha (la descarga B2 la observa como oficial),
    dejar de marcar PROXY es correcto (recuperación automática). El fallo de 559234b8
    era quitar la marca en fechas que la descarga no trajo como oficiales.
    """
    prev = _status_by_date(previous)
    pub = _status_by_date(published)
    official = set()
    if fetched_official is not None:
        official = set(_status_by_date(fetched_official))
    lost = set()
    for d, st in prev.items():
        if st != "PROXY":
            continue
        if d in official:
            continue
        if pub.get(d) != "PROXY":
            lost.add(d)
    return lost


class DetectorFlagsIncident(unittest.TestCase):
    def test_559234b8_shape_is_detected(self):
        lost = dropped_proxy_marks(PREV, DROPPED, fetched_official="Date,Value\n2026-11-12,2.8\n")
        self.assertEqual(
            lost,
            {"2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"},
        )

    def test_official_recovery_may_clear_only_fetched_dates(self):
        published = (
            "Date,Value,Status,Source,ProxyDate\n"
            "2026-11-12,2.8,,,\n"
            "2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13\n"
            "2026-11-16,2.75,PROXY,NZD_OCR,2026-11-13\n"
            "2026-11-17,2.75,PROXY,NZD_OCR,2026-11-13\n"
            "2026-11-18,2.75,PROXY,NZD_OCR,2026-11-13\n"
            "2026-11-19,2.75,PROXY,NZD_OCR,2026-11-13\n"
            "2026-11-20,2.81,,,\n"
        )
        self.assertEqual(dropped_proxy_marks(PREV, published, OFFICIAL_RECOVERY), set())
        # Si también se cayera el 13-nov, el detector debe fallar.
        bad = published.replace("2026-11-13,2.75,PROXY,NZD_OCR,2026-11-13\n", "2026-11-13,2.75,,,\n")
        self.assertEqual(dropped_proxy_marks(PREV, bad, OFFICIAL_RECOVERY), {"2026-11-13"})


class VpsPublicationKeepsProxy(unittest.TestCase):
    """Camino real de publicación VPS: macfetch.write empalma la historia conservando PROXY."""

    def test_macfetch_write_does_not_drop_prior_proxy_marks(self):
        from g8common import macfetch

        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "NZD_CASH_ON.csv")
        Path(path).write_text(PREV, encoding="utf-8")
        fetched = b"Date,Value\n2026-11-12,2.8\n"
        F = macfetch.Fetch("t", tmp, budget_s=5, now=lambda: 0, sleep=lambda s: None, log=lambda *_: None)
        self.assertTrue(F.write(path, fetched, expect_header=["Date", "Value"]))
        published = Path(path).read_text(encoding="utf-8")
        lost = dropped_proxy_marks(PREV, published, fetched_official=fetched.decode("utf-8"))
        self.assertEqual(lost, set(), "publicación VPS quitó PROXY en: %s\n%s" % (sorted(lost), published))
        self.assertIn("Date,Value,Status,Source,ProxyDate", published)
        for d in ("2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"):
            self.assertIn("%s,2.75,PROXY,NZD_OCR,2026-11-13" % d, published)

    def test_registry_route_is_rbnz_via_proxy_cf_not_status_proxy(self):
        # La vía de acceso no es el estado PROXY de las filas (ese sigue en NZD_CASH_ON.csv).
        with open(os.path.join(ROOT, "sources", "registry.csv"), encoding="utf-8") as fh:
            rows = {r["feed_id"]: r for r in csv.DictReader(fh)}
        for fid in ("NZD_NOM_RBNZ", "NZD_CASH_ON", "NZD_BILL_90D", "NZD_BOND_5Y", "NZD_IIB_2035"):
            self.assertIn("RBNZ vía proxy CF", rows[fid]["primary_source"], fid)
            self.assertNotIn("Mac local fetch", rows[fid]["primary_source"], fid)
        self.assertEqual(rows["NZD_CASH_ON"]["status"], "EXISTS_REPO")


if __name__ == "__main__":
    unittest.main()

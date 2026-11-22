"""t331u B4 — SWPT semanal (FRED por nombre de secreto) y cadencia CFTC (martes + flag bisemanal)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cftc_cadence as C  # noqa: E402
import fetch_swpt as S  # noqa: E402


class CftcCadence(unittest.TestCase):
    def test_position_date_es_martes(self):
        self.assertTrue(C.is_tuesday("2026-11-17"))
        self.assertFalse(C.is_tuesday("2026-11-20"))  # viernes de publicación
        fields = C.document_fields("2026-11-17", cfg={"enabled": False})
        self.assertEqual(fields["position_date"], "2026-11-17")
        self.assertEqual(fields["position_weekday"], "Tuesday")
        self.assertEqual(fields["cadence"], "weekly")
        self.assertFalse(fields["biweekly_enabled"])

    def test_flag_off_no_activa_aunque_tff_ya_tenga_dos_fechas_en_la_semana(self):
        dates = ["2026-11-17", "2026-11-19"]  # tue + thu same ISO week
        self.assertTrue(C.tff_fx_confirms_biweekly(dates))
        self.assertEqual(C.weeks_with_multiple_position_dates(dates), ["2026-W47"])
        self.assertFalse(C.should_activate(dates, cfg={"enabled": False}))
        self.assertTrue(C.should_activate(dates, cfg={"enabled": True}))
        # una sola fecha: aunque el flag esté on, aún no hay confirmación en TFF FX
        self.assertFalse(C.should_activate(["2026-11-17"], cfg={"enabled": True}))

    def test_default_file_is_disabled(self):
        cfg = C.load_flag(ROOT / "sources" / "cftc_cadence.json")
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["position_weekday"], "Tuesday")

    def test_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cftc_cadence.json"
            p.write_text('{"enabled": false}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"CFTC_BIWEEKLY": "1"}):
                self.assertTrue(C.load_flag(p)["enabled"])
            with mock.patch.dict(os.environ, {"CFTC_BIWEEKLY": "0"}):
                self.assertFalse(C.load_flag(p)["enabled"])


class SwptFetch(unittest.TestCase):
    def test_parse_json_and_csv_omiten_puntos(self):
        js = json.dumps({"observations": [
            {"date": "2026-11-05", "value": "0.000"},
            {"date": "2026-11-12", "value": "."},
            {"date": "2026-11-19", "value": "123.4"},
        ]})
        s = S.parse_fred_json(js)
        self.assertEqual(s["2026-11-05"], 0.0)
        self.assertNotIn("2026-11-12", s)
        self.assertEqual(s["2026-11-19"], 123.4)
        csv_text = "DATE,SWPT\n2026-11-05,0.0\n2026-11-12,.\n2026-11-19,123.4\n"
        self.assertEqual(S.parse_fredgraph_csv(csv_text)["2026-11-19"], 123.4)
        self.assertNotIn("2026-11-12", S.parse_fredgraph_csv(csv_text))

    def test_api_url_no_embebe_la_clave_en_fred_urls(self):
        urls = S.fred_urls()
        self.assertIn("series_id=SWPT", urls["api"])
        self.assertNotIn("api_key", urls["api"])
        self.assertIn("id=SWPT", urls["graph"])

    def test_usa_api_si_hay_clave_sin_escribir_el_valor(self):
        js = json.dumps({"observations": [{"date": "2026-11-19", "value": "10"}]})
        seen = []

        def opener(url):
            seen.append(url)
            if "api_key=" in url:
                return js.encode()
            raise AssertionError("no debería ir a fredgraph")

        with mock.patch.dict(os.environ, {"FRED_API_KEY": "TEST_KEY_VALUE"}):
            with tempfile.TemporaryDirectory() as td:
                out = S.run(data_dir=td, opener=opener)
                body = Path(td, "SWPT.csv").read_text(encoding="utf-8")
        self.assertEqual(out["channel"], "api")
        self.assertEqual(out["last"], "2026-11-19")
        self.assertIn("2026-11-19,10.0000,FRED.SWPT/api", body)
        self.assertNotIn("TEST_KEY_VALUE", body)
        self.assertTrue(any("api_key=TEST_KEY_VALUE" in u for u in seen))  # se usa, no se loguea

    def test_sin_clave_cae_a_fredgraph(self):
        csv_text = "DATE,SWPT\n2026-11-19,7.5\n"

        def opener(url):
            self.assertNotIn("api_key", url)
            self.assertIn("fredgraph", url)
            return csv_text.encode()

        with mock.patch.dict(os.environ, {"FRED_API_KEY": ""}, clear=False):
            os.environ.pop("FRED_API_KEY", None)
            with tempfile.TemporaryDirectory() as td:
                out = S.run(data_dir=td, opener=opener)
        self.assertEqual(out["channel"], "fredgraph")
        self.assertEqual(out["last"], "2026-11-19")


class RegistryLocks(unittest.TestCase):
    def test_swpt_sigue_not_ingested_y_mirror_existe_hasta_actualizar_f1(self):
        import csv
        with open(ROOT / "sources" / "registry.csv", encoding="utf-8") as fh:
            rows = {r["feed_id"]: r for r in csv.DictReader(fh)}
        self.assertEqual(rows["SWPT"]["status"], "NOT_INGESTED")
        self.assertEqual(rows["USD_TP_MIRROR"]["status"], "EXISTS_BROWSER")
        self.assertIn("fetch_swpt.py", rows["SWPT"]["notes"])
        self.assertIn("RETIRADO t331u B4", rows["USD_TP_MIRROR"]["notes"])


if __name__ == "__main__":
    unittest.main()

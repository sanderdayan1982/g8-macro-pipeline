"""Exclusiones y componentes intactos: opciones CME / Databento / S01B (F6 pendiente) / motor de alertas actual /
descargadores del Mac. Deben seguir byte a byte como en main ed9ed64 y no importar g8common."""
import hashlib
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FROZEN = [('scripts/cme_options_collector.py', '61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9'), ('scripts/build_options_summary.py', 'f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019'), ('scripts/fx_futures_collector.py', '3aeb126b7889d03530ccf4f0548e5a233b980c229d701866c51d480fbf335e83'), ('.github/workflows/cme_options.yml', '97bb2c816fe25146f35e2101afb1d9f0be4346458e11c3d44aac32620fecc4b3'), ('.github/workflows/fx_futures_backfill.yml', '5e2310241db474367876158e5e1f6128c424a361b8aa3dd5ce3862cbe7392089'), ('scripts/dashboard_alerts.py', '8569a4753c00a560a100ab6d98477b9ea911dae678edf4dae88e37264cd67d10'), ('scripts/s01b.py', 'd8ae5ad1a89c0e8e70d53d08731b706a53cd4baa10686f31f86123754170ae34'), ('scripts/usd_factor.py', 'd96bbaa366637a5d1238eac2af1e03096561ff63d92fc3bc246919bad804d3d3'), ('scripts/book_risk.py', '78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50'), ('scripts/fetch_nzd_b2.py', '2b0554608c9be43f3e4a890a67c18062c03712e204392da30b7a67951c8aeb63'), ('scripts/fetch_chf_snb.py', 'defc8cf0bbe1f354e26975daa4629770eed24ef64821b696fe352322104b7dfb')]


class Exclusions(unittest.TestCase):
    def test_untouched(self):
        for f, h in FROZEN:
            with open(os.path.join(ROOT, f), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), h, f)

    def test_options_and_databento_do_not_import_common_modules(self):
        for f in ("scripts/cme_options_collector.py", "scripts/build_options_summary.py", "scripts/fx_futures_collector.py"):
            self.assertNotIn("g8common", open(os.path.join(ROOT, f), encoding="utf-8").read(), f)


if __name__ == "__main__":
    unittest.main()

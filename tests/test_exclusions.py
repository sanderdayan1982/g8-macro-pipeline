"""Exclusiones y componentes intactos: opciones CME / Databento / S01B (F6 pendiente) / motor de alertas actual /
factor USD. Deben seguir byte a byte como en main ed9ed64 y no importar g8common.
Acta P-1 (autorizada 1-oct): dashboard_alerts.py v2.6 — nueva huella (regla de coherencia tipo a un día ↔ oficial).
Acta P-3 opción A (autorizada 1-oct): dashboard_alerts.py (flag «TP curva arrastrada (ffill)») y s01b.py (quality_ok ignora
el sufijo _FFILL; emisión idéntica) — nuevas huellas.
Acta P-7 (autorizada 1-oct): cme_options.yml — solo el push final, con reintento (scripts/tools/git_push_retry.sh).
Lote 3B (autorizado 24-sep): los descargadores del Mac (fetch_nzd_b2, fetch_chf_snb) dejan de estar congelados."""
import hashlib
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FROZEN = [('scripts/cme_options_collector.py', '61276d17636d921913e383127b47176e946adc5eab59420416f4f3a244a477f9'), ('scripts/build_options_summary.py', 'f0fdf61d1047d3d899b5037f3047dc194329b4a4d5de42b59bed1346e304e019'), ('scripts/fx_futures_collector.py', '3aeb126b7889d03530ccf4f0548e5a233b980c229d701866c51d480fbf335e83'), ('.github/workflows/cme_options.yml', '281dfed01663e6147cd32fdde02c1bc88688ccdee0f6a54789128205e0fab6ab'), ('.github/workflows/fx_futures_backfill.yml', '5e2310241db474367876158e5e1f6128c424a361b8aa3dd5ce3862cbe7392089'), ('scripts/dashboard_alerts.py', '6dfee9f83d4bb3bcb3c03bfbfe57de54ab40835924a51e5329e785b676b5477d'), ('scripts/s01b.py', '7c6f3b7e758f9f95fc6777b76d395173892cc5ec354627beb2c0a7b35e81768d'), ('scripts/usd_factor.py', 'd96bbaa366637a5d1238eac2af1e03096561ff63d92fc3bc246919bad804d3d3'), ('scripts/book_risk.py', '78514c2daa319d08454a99dae8225f7c2208da1fcd34cab13eb2547d72ef9d50')]


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

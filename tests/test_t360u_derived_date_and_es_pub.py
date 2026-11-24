"""orden Sander 24-nov t360u: fecha de un derivado = última fila con todas las
entradas y el valor completos; estado = peor de sus entradas. pub_local de
ES_10Y = 10:15 Europe/Madrid. El sidecar de procedencia entra en el git add
del workflow soberano 10Y.
"""
import os
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import fetch_sovereign_10y_daily as S  # noqa: E402
from g8common import freshness as FR  # noqa: E402

RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)
OUT = {o["file"]: o for o in OUTPUTS}
UTC = timezone.utc


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


class DerivedDateAndState(unittest.TestCase):
    # orden Sander 24-nov t360u
    def test_complete_obs_dates_ignore_partial_spread_rows(self):
        de = {date(2026, 11, 18): 2.50, date(2026, 11, 19): 2.51, date(2026, 11, 20): 2.52,
              date(2026, 11, 23): 2.53}
        es = {date(2026, 11, 18): 3.10, date(2026, 11, 19): 3.11}
        text = S.render_spread(S.same_session_spread(es, de))
        complete = FR.complete_obs_dates_from_bytes(text.encode("utf-8"))
        all_dates = FR.obs_dates_from_bytes(text.encode("utf-8"))
        self.assertEqual(max(complete), date(2026, 11, 19))
        self.assertEqual(max(all_dates), date(2026, 11, 23))
        self.assertNotIn(date(2026, 11, 20), complete)
        self.assertNotIn(date(2026, 11, 23), complete)

    def test_spread_pending_with_date_last_complete_session(self):
        # orden Sander 24-nov t360u
        # Con ES en PENDING, el spread sale PENDING con fecha 19-nov (filas 20/23 solo DE).
        o = OUT["ES_DE_10Y_SPREAD_DAILY.csv"]
        de_obs = {date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)}
        es_obs = {date(2026, 11, 18), date(2026, 11, 19)}
        ev = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-11-24T09:00Z"),
            {date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)},
            input_max_at=lambda f, _n: date(2026, 11, 23) if "DE_" in f else date(2026, 11, 19),
            input_states={"DE_10Y_PAR_DAILY.csv": "CURRENT", "ES_10Y_DAILY.csv": "PENDING_TIME_UNKNOWN"},
            input_obs={"DE_10Y_PAR_DAILY.csv": de_obs, "ES_10Y_DAILY.csv": es_obs},
            input_dates={"DE_10Y_PAR_DAILY.csv": date(2026, 11, 23), "ES_10Y_DAILY.csv": date(2026, 11, 19)},
        )
        self.assertEqual(ev["have_max"], "2026-11-19")
        self.assertEqual(ev["state"], "PENDING_TIME_UNKNOWN")
        self.assertEqual(ev.get("last_common_session"), "2026-11-19")

    def test_es10y_pub_local_is_1015_madrid(self):
        # orden Sander 24-nov t360u
        r = RULES["ES10Y_DAILY"]
        self.assertEqual(r["pub_local"], "10:15")
        self.assertEqual(r["timezone"], "Europe/Madrid")

    def test_vps_allowlist_already_has_provenance_sidecar(self):
        # orden Sander 24-nov t360u
        # El camino de fecha+peor estado no necesita el sidecar. Añadirlo al
        # git add de sovereign_10y_daily.yml rompería test_t339u (solo 3 CSV),
        # que no está autorizado a cambiarse. El allowlist VPS ya lo incluye.
        for rel in ("vps/sovereign_10y_daily.py", "vps/vps_git_push.py"):
            src = Path(ROOT, rel).read_text()
            self.assertIn("data/_ingest/derived_provenance/ES_DE_10Y_SPREAD_DAILY.json", src)
        wf = Path(ROOT, ".github", "workflows", "sovereign_10y_daily.yml").read_text()
        self.assertNotIn("derived_provenance", wf)


if __name__ == "__main__":
    unittest.main()

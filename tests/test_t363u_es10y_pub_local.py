"""orden Sander 24-nov t360u/t363u (PR B): pub_local de ES_10Y = 10:15
Europe/Madrid, con g_min=60 como DE10Y_PAR.
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from g8common import freshness as FR  # noqa: E402

RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)


class Es10yPublicationClock(unittest.TestCase):
    # orden Sander 24-nov t360u/t363u
    def test_es10y_pub_local_1015_madrid_and_g_min_60(self):
        r = RULES["ES10Y_DAILY"]
        p = PARAMS["ES10Y_DAILY"]
        self.assertEqual(r["pub_local"], "10:15")
        self.assertEqual(r["timezone"], "Europe/Madrid")
        self.assertEqual(p["g_min"], "60")
        # mismo margen operativo que DE
        self.assertEqual(PARAMS["DE10Y_PAR"]["g_min"], "60")


if __name__ == "__main__":
    unittest.main()

"""process_session must not raise NameError on the option flag.

Invented rows only. No network and no DATABENTO_API_KEY.
orden Sander 18-nov t255u.
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cme_options_collector as C  # noqa: E402

SESSION = date(2020, 1, 6)
SESSION_ISO = "2020-01-06"


def _stat_row(iid, stat_type):
    row = {col: "" for col in C.STATS_FINGERPRINT}
    row.update({
        "instrument_id": iid,
        "ts_ref": SESSION_ISO + "T00:00:00Z",
        "price": "1",
        "quantity": "1",
        "stat_type": stat_type,
        "stat_flags": "1",
        "symbol": "FAKE" + iid,
    })
    return row


def _invented_pulls():
    """One statistics list and one definition list per parent. Not market data."""
    pulls = {}
    n = 0
    for parent in C.OPT_PARENTS + C.FUT_PARENTS:
        is_opt = parent.endswith(".OPT")
        count = C.MIN_OPT_SETTLES if is_opt else C.MIN_FUT_SETTLES
        oi_n = C.MIN_OPT_OI_ROWS if is_opt else 0
        stats, defs = [], []
        for i in range(count):
            n += 1
            iid = "i%d" % n
            stats.append(_stat_row(iid, C.STAT_SETTLE))
            if i < oi_n:
                stats.append(_stat_row(iid, C.STAT_OI))
            defs.append({
                "instrument_id": iid,
                "strike_price": "10" if is_opt else "",
                "expiration": "2020-06-01T00:00:00Z",
                "instrument_class": "C" if is_opt else "F",
                "symbol": "FAKE" + iid,
            })
        pulls[parent] = {"statistics": stats, "definition": defs}
    return pulls


class ProcessSessionNameError(unittest.TestCase):
    def test_process_session_does_not_raise_name_error(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        pulls = _invented_pulls()

        def pull_csv_one(auth, schema, parent, start, end):
            return pulls[parent][schema]

        def refuse_network(*_args, **_kwargs):
            raise AssertionError("network was called")

        with patch.object(C, "OUT_DIR", Path(tmp) / "canon"), \
                patch.object(C, "VOLUME_DIR", Path(tmp) / "vol"), \
                patch.object(C, "guard_availability",
                             return_value=datetime(2020, 1, 8, tzinfo=timezone.utc)), \
                patch.object(C, "guard_cost", return_value=0.01), \
                patch.object(C, "pull_csv_one", side_effect=pull_csv_one), \
                patch.object(C, "api_get", side_effect=refuse_network):
            try:
                result = C.process_session(("unused", ""), SESSION)
            except NameError as exc:
                self.fail("NameError: %s" % exc)
        self.assertIs(result, True)


if __name__ == "__main__":
    unittest.main()

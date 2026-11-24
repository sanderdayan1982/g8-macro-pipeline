"""t361u: el state de snb_daily no puede fallar con TypeError cuando run() devuelve ok."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "vps"))

import snb_daily as S  # noqa: E402


def _run_result():
    return {
        "ok": True,
        "last_obs": "2026-11-19",
        "publishing_date": "2026-11-20 08:31",
        "push": {"sha": "abc123", "changed": True, "files": ["data/CHF_SPOT_1Y_DAILY.csv"]},
        "n_files": 30,
    }


class T361uSnbStateOk(unittest.TestCase):
    def test_legacy_update_raises_when_result_already_has_ok(self):
        # orden Sander 24-nov t361u
        """Reproduce el TypeError del 22-nov 20:45 WAT: run() ya trae ok=True
        y rec.update(ok=True, **result_sin_push) pasa ok dos veces."""
        rec = {"job": "snb1a", "ok": False, "rc": 1, "consecutive_fail": 0}
        result = _run_result()
        with self.assertRaises(TypeError) as ctx:
            rec.update(
                ok=True,
                rc=0,
                consecutive_fail=0,
                **{k: result[k] for k in result if k != "push"},
            )
        self.assertIn("multiple values for keyword argument 'ok'", str(ctx.exception))

    def test_current_main_accepts_run_result_that_includes_ok(self):
        # orden Sander 24-nov t361u
        """El arreglo de #110 (asignaciones rec['ok']=True, sin update) debe
        escribir el state cuando run() devuelve ok, sin TypeError."""
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, "snb1a.json")
        lock = os.path.join(tmp, "snb.lock")
        result = _run_result()
        env = {"G8_FULL": "dummy", "G8_SNB_LOCK": lock}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            S, "run", return_value=result
        ):
            rc = S.main(["--workdir", tmp, "--state", state])
        self.assertEqual(rc, 0)
        rec = json.loads(Path(state).read_text(encoding="utf-8"))
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["rc"], 0)
        self.assertEqual(rec["consecutive_fail"], 0)
        self.assertEqual(rec["last_obs"], "2026-11-19")
        self.assertEqual(rec["publishing_date"], "2026-11-20 08:31")
        self.assertEqual(rec["n_files"], 30)
        self.assertEqual(rec["sha"], "abc123")
        self.assertNotIn("error_type", rec)
        self.assertNotIn("error", rec)


if __name__ == "__main__":
    unittest.main()

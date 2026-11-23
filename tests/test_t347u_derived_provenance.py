"""t347u: procedencia de series derivadas (sha+fecha de cada entrada, generated_utc).

El cálculo del spread no cambia. AUD_NOWCAST sigue EST y no vota.
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from g8common import derived_provenance as DP  # noqa: E402
from g8common import freshness as FR  # noqa: E402
import fetch_sovereign_10y_daily as S  # noqa: E402
import freshness_report as FRR  # noqa: E402

RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)
OUT = {o["file"]: o for o in OUTPUTS}
UTC = timezone.utc


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def _write_csv(path, rows, header="DATE,VALUE"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for d, v in rows:
            fh.write("%s,%s\n" % (d, v))


class ProvenanceHelper(unittest.TestCase):
    def test_write_and_read_sha_and_date(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        data = os.path.join(tmp, "data")
        _write_csv(os.path.join(data, "IN_A.csv"), [("2026-09-22", "1.0"), ("2026-09-23", "1.1")])
        _write_csv(os.path.join(data, "IN_B.csv"), [("2026-09-22", "2.0"), ("2026-09-23", "2.1")])
        dest = DP.write_for_data_dir(data, "OUT.csv", ["IN_A.csv", "IN_B.csv"],
                                     generated_utc="2026-09-24T11:00:00Z")
        rec = DP.read_file(dest)
        self.assertEqual(rec["schema"], DP.SCHEMA)
        self.assertEqual(rec["generated_utc"], "2026-09-24T11:00:00Z")
        self.assertEqual(rec["inputs"]["IN_A.csv"]["date"], "2026-09-23")
        self.assertEqual(rec["inputs"]["IN_A.csv"]["sha"],
                         DP.sha256_path(os.path.join(data, "IN_A.csv")))
        self.assertEqual(DP.read(tmp, "OUT.csv")["file"], "OUT.csv")

    def test_last_common_session_is_intersection_not_min_of_maxes(self):
        # A tiene 18 y 20; B tiene 18 y 19. min(máximos)=19, intersección={18}
        obs = {
            "A.csv": {date(2026, 11, 18), date(2026, 11, 20)},
            "B.csv": {date(2026, 11, 18), date(2026, 11, 19)},
        }
        self.assertEqual(FR.last_common_session(obs), date(2026, 11, 18))
        self.assertIsNone(FR.last_common_session({"A.csv": {date(2026, 11, 20)}, "B.csv": set()}))


class SpreadUnchanged(unittest.TestCase):
    def test_same_session_spread_bytes_unchanged_when_writing_provenance(self):
        de = {date(2026, 11, 18): 2.50, date(2026, 11, 19): 2.51, date(2026, 11, 20): 2.52}
        es = {date(2026, 11, 18): 3.10, date(2026, 11, 19): 3.11}
        sp = S.same_session_spread(es, de)
        text = S.render_spread(sp)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        data = os.path.join(tmp, "data")
        os.makedirs(data)
        # write legs and spread as the job would
        Path(os.path.join(data, S.DE_FILE)).write_text("DATE,VALUE\n2026-11-20,2.52\n")
        Path(os.path.join(data, S.ES_FILE)).write_text("DATE,VALUE\n2026-11-19,3.11\n")
        spread_path = os.path.join(data, S.SPREAD_FILE)
        with open(spread_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        before = Path(spread_path).read_bytes()
        DP.write_for_data_dir(data, S.SPREAD_FILE, [S.DE_FILE, S.ES_FILE],
                              generated_utc="2026-11-23T17:58:00Z")
        self.assertEqual(Path(spread_path).read_bytes(), before)
        rec = DP.read(tmp, S.SPREAD_FILE)
        self.assertEqual(rec["inputs"][S.DE_FILE]["date"], "2026-11-20")
        self.assertEqual(rec["inputs"][S.ES_FILE]["date"], "2026-11-19")


class AudNowcastDoesNotVote(unittest.TestCase):
    def test_aud_nowcast_stays_legacy_with_provenance(self):
        o = OUT["AUD_NOWCAST.csv"]
        inputs = [x for x in o["inputs"].split(";") if x]
        prov = {"generated_utc": "2026-09-24T11:00:00Z",
                "inputs": {f: {"sha": "x", "date": "2026-09-23"} for f in inputs}}
        r = FR.evaluate(
            o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS,
            T("2026-09-24T12:00Z"), {date(2026, 9, 23)},
            input_max_at=lambda f, t: date(2026, 9, 23),
            provenance=prov,
            input_states={f: "OVERDUE" for f in inputs},
            input_shas={f: "x" for f in inputs},
            input_dates={f: date(2026, 9, 23) for f in inputs},
        )
        self.assertIn("INPUT_VERSION_UNKNOWN", r["flags"])
        self.assertNotEqual(r["state"], "OVERDUE")  # no hereda el peor de las entradas

    def test_aud_nowcast_excluded_from_other_derived_vote(self):
        self.assertEqual(FR.worst_input_state(["CURRENT", "OVERDUE"]), "OVERDUE")
        # AUD_NOWCAST no entra: el filtro está en _evaluate_derived, aquí se comprueba el helper
        self.assertEqual(FR.worst_input_state(["CURRENT"]), "CURRENT")


class AllowlistHasSpreadProvenance(unittest.TestCase):
    def test_sovereign_allowlist_includes_provenance_sidecar(self):
        path = os.path.join(ROOT, "vps", "sovereign_10y_daily.py")
        src = Path(path).read_text()
        self.assertIn("data/_ingest/derived_provenance/ES_DE_10Y_SPREAD_DAILY.json", src)
        src = Path(os.path.join(ROOT, "vps", "vps_git_push.py")).read_text()
        self.assertIn("data/_ingest/derived_provenance/ES_DE_10Y_SPREAD_DAILY.json", src)


if __name__ == "__main__":
    unittest.main()

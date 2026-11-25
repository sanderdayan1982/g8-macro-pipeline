"""t385u: el brief privado con claves BRIEF_KEYS no se publica. Guardián intacto."""
import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))
import private_data as PD  # noqa: E402
import dashboard_alerts as DA  # noqa: E402


# Misma forma que la fuga (nombres de BRIEF_KEYS); valores sintéticos, no de fuente.
LEAKED_BRIEF = {
    "generated_utc": "2026-01-01T00:00:00Z",
    "as_of": {"rates": "2026-01-01"},
    "walls": [
        {
            "pair": "AA/BB",
            "pin": 0,
            "call": 0,
            "put": 0,
            "strikes_c": 0,
            "strikes_p": 0,
            "front_oi": 0,
            "top": [{"oi": 0}, {"oi": 0}, {"oi": 0}],
        }
    ],
}


def _count(obj, name):
    n = 0
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == name:
                n += 1
            n += _count(value, name)
    elif isinstance(obj, list):
        for item in obj:
            n += _count(item, name)
    return n


class PublicBriefGuard(unittest.TestCase):
    def test_guardian_keyset_matches_recorte(self):
        tree = ast.parse((ROOT / "tests/test_cme_public_guardian.py").read_text(encoding="utf-8"))
        ns = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("SHARED_KEYS", "BRIEF_KEYS") for t in node.targets
            ):
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<guardian>", "exec"), ns)
        self.assertEqual(set(ns["SHARED_KEYS"]), set(PD.SHARED_KEYS))
        self.assertEqual(set(ns["BRIEF_KEYS"]), set(PD.BRIEF_KEYS))

    def test_recorte_clears_the_nine_brief_keys(self):
        found = PD.collect_keys(LEAKED_BRIEF)
        hit = PD.forbidden_in_doc(LEAKED_BRIEF)
        self.assertEqual(hit, sorted(PD.BRIEF_KEYS & found))
        self.assertEqual(len(hit), 9)
        self.assertEqual(_count(LEAKED_BRIEF, "walls"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "call"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "put"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "pin"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "strikes_c"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "strikes_p"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "front_oi"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "top"), 1)
        self.assertEqual(_count(LEAKED_BRIEF, "oi"), 3)
        out = PD.public_projection(LEAKED_BRIEF)
        self.assertEqual(PD.forbidden_in_doc(out), [])
        self.assertIn("generated_utc", out)
        self.assertIn("as_of", out)
        self.assertNotIn("walls", out)
        # el recorte no muta el documento de origen
        self.assertEqual(PD.forbidden_in_doc(LEAKED_BRIEF), hit)

    def test_apply_public_alerts_stashes_private_and_rewrites_workspace(self):
        tmp = tempfile.mkdtemp(prefix="t385u-brief-")
        try:
            public = Path(tmp) / "data" / "alerts" / "brief.json"
            public.parent.mkdir(parents=True)
            public.write_text(json.dumps(LEAKED_BRIEF), encoding="utf-8")
            Path(tmp, "data/alerts/state.json").write_text("{}", encoding="utf-8")
            remaining, stashed = PD.apply_public_alerts(tmp)
            self.assertEqual(remaining, [])
            self.assertEqual(stashed, 1)
            self.assertEqual(PD.forbidden_in_path(str(public)), [])
            stash = Path(tmp) / "data" / "alerts" / "private" / "brief.json"
            self.assertTrue(stash.is_file())
            self.assertEqual(len(PD.forbidden_in_path(str(stash))), 9)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_exclude_drops_brief_if_keys_remain(self):
        tmp = tempfile.mkdtemp(prefix="t385u-excl-")
        try:
            public = Path(tmp) / "data" / "alerts" / "brief.json"
            public.parent.mkdir(parents=True)
            public.write_text(json.dumps(LEAKED_BRIEF), encoding="utf-8")
            change = (
                "data/USD_FACTOR.json",
                "data/alerts/brief.json",
                "data/alerts/private/brief.json",
            )
            out = PD.exclude_forbidden_public_alerts(change, tmp)
            self.assertEqual(out, ("data/USD_FACTOR.json",))
            public.write_text(json.dumps(PD.public_projection(LEAKED_BRIEF)), encoding="utf-8")
            out2 = PD.exclude_forbidden_public_alerts(change, tmp)
            self.assertEqual(out2, ("data/USD_FACTOR.json", "data/alerts/brief.json"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_write_public_and_private_uses_recorte(self):
        tmp = tempfile.mkdtemp(prefix="t385u-write-")
        try:
            public = os.path.join(tmp, "brief.json")
            DA._write_public_and_private(public, LEAKED_BRIEF)
            self.assertEqual(PD.forbidden_in_path(public), [])
            stash = os.path.join(tmp, "private", "brief.json")
            self.assertEqual(len(PD.forbidden_in_path(stash)), 9)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_usd_factor_vps_applies_projection_on_success(self):
        src = (ROOT / "vps/usd_factor_vps.py").read_text(encoding="utf-8")
        run = src[src.index("def run(workdir):"):]
        self.assertIn("PD.apply_public_alerts(workdir)", run)
        self.assertIn("PD.exclude_forbidden_public_alerts(change, workdir)", run)
        self.assertLess(run.index("PD.apply_public_alerts(workdir)"), run.index("P.commit_and_push"))
        self.assertIn('if dash_rc != 0:', run)
        self.assertIn('"data/alerts/state.json", "data/alerts/brief.json"', run)
        # la copia al repo privado sale de private/, no del path público
        self.assertIn('os.path.join(workdir, "data", "alerts", "private"', run)

    def test_usd_factor_yml_runs_public_alerts_before_git_add(self):
        yml = (ROOT / ".github/workflows/usd_factor.yml").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/tools/private_data.py public-alerts", yml)
        self.assertLess(
            yml.index("python3 scripts/tools/private_data.py public-alerts"),
            yml.index("git add data/usd_factor data/USD_FACTOR.json data/BOOK_RISK.json data/alerts"),
        )
        self.assertIn("git reset -q -- data/alerts/private", yml)
        self.assertIn("--from-private-copies", yml)
        self.assertLess(
            yml.index("git add data/usd_factor data/USD_FACTOR.json data/BOOK_RISK.json data/alerts"),
            yml.index("--from-private-copies"),
        )

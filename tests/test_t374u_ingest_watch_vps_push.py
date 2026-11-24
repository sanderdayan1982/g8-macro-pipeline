"""t374u, orden Sander 24-nov: ingest_watch reconoce publicaciones [vps-push] por la ruta del feed y el tag.

Un commit [vps-push] que toca la ruta del feed es una publicación (igual que el prefijo de Actions);
un commit [vps-push] solo de registros data/_ingest/ (verify/steps) no lo es. «Nunca dos publicaciones
del mismo feed en el mismo turno» se mantiene: VPS+VPS o Actions+VPS por encima de los huecos = incidencia.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ingest_watch as W  # noqa: E402

USDF = ("data/USD_FACTOR.json",)
MFV = ("data/MFV_G8_state.json",)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Repo:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.check_call(cmd, cwd=self.root)
        self.n = 0

    def commit(self, when, msg, *paths):
        self.n += 1
        for rel in paths:
            f = self.root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("v%d\n" % self.n, encoding="utf-8")
            subprocess.check_call(["git", "add", rel], cwd=self.root)
        env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        subprocess.check_call(["git", "commit", "-q", "-m", msg], cwd=self.root, env=env)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()


def usdf(root):
    return W.list_publish_commits(root, USDF, "chore: usd factor update")


class VpsRecognition(unittest.TestCase):
    def test_vps_commit_touching_feed_path_is_a_publication(self):
        r = Repo()
        sha = r.commit("2026-11-24T15:30:29+00:00", "[vps-push] usd factor update 2026-11-24 [skip netlify]",
                       "data/USD_FACTOR.json", "data/BOOK_RISK.json", "data/_ingest/watch_state.json")
        rows = usdf(r.root)
        self.assertEqual([c[0] for c in rows], [sha])
        self.assertEqual(rows[0][1], utc(2026, 11, 24, 15, 30, 29))

    def test_ingest_only_vps_commit_is_not_a_publication(self):
        r = Repo()
        pub = r.commit("2026-11-24T15:30:29+00:00", "[vps-push] usd factor update 2026-11-24 [skip netlify]",
                       "data/USD_FACTOR.json")
        r.commit("2026-11-24T15:30:36+00:00", "[vps-push] usd_factor registros verify/steps 2026-11-24 [skip netlify]",
                 "data/_ingest/latest/actions__job_usd_factor_steps.json",
                 "data/_ingest/latest/actions__job_verify_usd_factor.json")
        r.commit("2026-11-24T16:07:18+00:00", "[vps-push] vps units status 2026-11-24T16:07:18Z [skip netlify]",
                 "data/_ingest/vps_units.json")
        self.assertEqual([c[0] for c in usdf(r.root)], [pub])

    def test_ingest_paths_never_count_as_feed_paths(self):
        self.assertEqual(W.feed_paths(("data/USD_FACTOR.json", "data/_ingest/latest/x.json")),
                         ("data/USD_FACTOR.json",))
        r = Repo()
        r.commit("2026-11-24T15:30:36+00:00", "[vps-push] usd_factor registros verify/steps 2026-11-24 [skip netlify]",
                 "data/_ingest/latest/actions__job_verify_usd_factor.json")
        self.assertEqual(W.list_publish_commits(r.root, ("data/_ingest/latest/actions__job_verify_usd_factor.json",),
                                                "chore: usd factor update"), [])

    def test_untagged_commit_on_feed_path_is_not_a_vps_publication(self):
        r = Repo()
        r.commit("2026-11-24T15:30:29+00:00", "g8-port: state update", "data/USD_FACTOR.json")
        self.assertEqual(usdf(r.root), [])

    def test_actions_prefix_still_recognized(self):
        r = Repo()
        sha = r.commit("2026-11-23T15:31:13+00:00", "chore: usd factor update 2026-11-23", "data/USD_FACTOR.json")
        self.assertEqual([c[0] for c in usdf(r.root)], [sha])

    def test_metals_shadow_vps_commit_is_not_the_weekly_feed(self):
        r = Repo()
        r.commit("2026-11-24T22:45:40+00:00", "[vps-push] metals-shadow fair value 2026-11-24 compute=ok [skip netlify]",
                 "data/MFV_G8_state.json", "data/MFV_G8_shadow_state.json")
        self.assertEqual(W.list_publish_commits(r.root, MFV, "chore: weekly metals fair value update",
                                                "[vps-push] metals fair value"), [])


class VpsDuplicateTurn(unittest.TestCase):
    def test_two_vps_usd_factor_in_two_slots_is_ok(self):
        r = Repo()
        r.commit("2026-11-24T15:30:29+00:00", "[vps-push] usd factor update 2026-11-24 [skip netlify]", *USDF)
        r.commit("2026-11-24T17:30:25+00:00", "[vps-push] usd factor update 2026-11-24 [skip netlify]", *USDF)
        alerts = {}
        W.check_duplicate_turn_publishes(r.root, utc(2026, 11, 24, 21, 0), alerts, [])
        self.assertNotIn("actions:usd_factor:duplicate_turn", alerts)

    def test_two_vps_publications_same_turn_flag(self):
        r = Repo()
        r.commit("2026-11-28T07:08:06+00:00", "[vps-push] metals fair value 2026-11-28 compute=ok [skip netlify]", *MFV)
        r.commit("2026-11-28T09:01:00+00:00", "[vps-push] metals fair value 2026-11-28 compute=ok [skip netlify]", *MFV)
        alerts = {}
        W.check_duplicate_turn_publishes(r.root, utc(2026, 11, 28, 12, 0), alerts, [])
        self.assertIn("actions:metals:duplicate_turn", alerts)
        self.assertIn("mismo feed publicado dos veces en el mismo turno", alerts["actions:metals:duplicate_turn"])
        self.assertIn("2 publicaciones", alerts["actions:metals:duplicate_turn"])

    def test_three_vps_usd_factor_same_turn_flag(self):
        r = Repo()
        for when in ("2026-11-24T15:30:29+00:00", "2026-11-24T17:30:25+00:00", "2026-11-24T18:02:00+00:00"):
            r.commit(when, "[vps-push] usd factor update 2026-11-24 [skip netlify]", *USDF)
        alerts = {}
        W.check_duplicate_turn_publishes(r.root, utc(2026, 11, 24, 21, 0), alerts, [])
        self.assertIn("3 publicaciones", alerts["actions:usd_factor:duplicate_turn"])

    def test_one_actions_plus_one_vps_same_turn_flag(self):
        r = Repo()
        a = r.commit("2026-11-28T07:08:06+00:00", "chore: weekly metals fair value update 2026-11-28", *MFV)
        v = r.commit("2026-11-28T07:20:00+00:00", "[vps-push] metals fair value 2026-11-28 compute=ok [skip netlify]",
                     *MFV)
        alerts = {}
        W.check_duplicate_turn_publishes(r.root, utc(2026, 11, 28, 12, 0), alerts, [])
        text = alerts["actions:metals:duplicate_turn"]
        self.assertIn(a[:8], text)
        self.assertIn(v[:8], text)

    def test_ingest_only_vps_commit_does_not_make_a_duplicate(self):
        r = Repo()
        r.commit("2026-11-28T07:08:06+00:00", "[vps-push] metals fair value 2026-11-28 compute=ok [skip netlify]", *MFV)
        r.commit("2026-11-28T07:09:00+00:00", "[vps-push] metals registro verify_metals 2026-11-28 [skip netlify]",
                 "data/_ingest/latest/actions__job_verify_metals.json")
        r.commit("2026-11-28T08:00:00+00:00", "[vps-push] metals fair value 2026-11-28 compute=fallo [skip netlify]",
                 "data/_ingest/metals_provenance.json", "data/alerts/brief.json")
        alerts = {}
        W.check_duplicate_turn_publishes(r.root, utc(2026, 11, 28, 12, 0), alerts, [])
        self.assertNotIn("actions:metals:duplicate_turn", alerts)


if __name__ == "__main__":
    unittest.main()

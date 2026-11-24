"""t352u / t353u / t354u, orden expresa Sander: incidencia «mismo feed publicado dos veces en el mismo turno»."""
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


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class CycleWindow(unittest.TestCase):
    def test_usd_factor_cycle_is_15_30_to_next_15_30(self):
        start, end = W.cycle_window("30 15 * * 1-5", utc(2026, 11, 20, 23, 20))
        self.assertEqual(start, utc(2026, 11, 20, 15, 30))
        self.assertEqual(end, utc(2026, 11, 23, 15, 30))  # 21-22 weekend, next is Monday 23

    def test_metals_weekly_is_saturday_0700(self):
        start, end = W.cycle_window("0 7 * * 6", utc(2026, 11, 21, 16, 0))
        self.assertEqual(start, utc(2026, 11, 21, 7, 0))  # Saturday
        self.assertEqual(end.weekday(), 5)  # next Saturday
        self.assertEqual(end.hour, 7)


class DuplicateTurn(unittest.TestCase):
    def test_two_metals_commits_same_saturday_flag(self):
        commits = [
            ("aaa11111", utc(2026, 11, 21, 7, 8, 6), "chore: weekly metals fair value update 2026-11-21"),
            ("bbb22222", utc(2026, 11, 21, 14, 59, 32), "chore: weekly metals fair value update 2026-11-21"),
        ]
        start, end = W.cycle_window("0 7 * * 6", utc(2026, 11, 21, 16, 0))
        self.assertEqual(len(W.duplicate_turn_alerts(commits, start, end, 1)), 2)

    def test_one_commit_is_ok(self):
        commits = [("aaa11111", utc(2026, 11, 21, 7, 8, 6), "chore: weekly metals fair value update 2026-11-21")]
        start, end = W.cycle_window("0 7 * * 6", utc(2026, 11, 21, 16, 0))
        self.assertEqual(W.duplicate_turn_alerts(commits, start, end, 1), [])

    def test_usd_factor_four_commits_in_cycle_flag(self):
        commits = [
            ("a", utc(2026, 11, 20, 15, 31), "chore: usd factor update 2026-11-20"),
            ("b", utc(2026, 11, 20, 17, 33), "chore: usd factor update 2026-11-20"),
            ("c", utc(2026, 11, 20, 22, 17), "chore: usd factor update 2026-11-20"),
            ("d", utc(2026, 11, 20, 23, 16), "chore: usd factor update 2026-11-20"),
        ]
        start, end = W.cycle_window("30 15 * * 1-5", utc(2026, 11, 20, 23, 30))
        self.assertEqual(len(W.duplicate_turn_alerts(commits, start, end, 2)), 4)

    def test_daily_delayed_next_morning_counts_in_previous_cycle(self):
        commits = [
            ("a", utc(2026, 11, 20, 21, 39), "chore: daily data update 2026-11-20 [135 files]"),
            ("b", utc(2026, 11, 21, 2, 37), "chore: daily data update 2026-11-21 [112 files]"),
        ]
        start, end = W.cycle_window("30 21 * * 1-5", utc(2026, 11, 21, 12, 0))
        # 21:30 Fri 20-nov to 21:30 Mon 23-nov (weekend)
        self.assertEqual(start, utc(2026, 11, 20, 21, 30))
        self.assertEqual(len(W.duplicate_turn_alerts(commits, start, end, 1)), 2)

    def test_check_writes_the_literal_incidence_text(self):
        tmp = Path(tempfile.mkdtemp())
        subprocess.check_call(["git", "init", "-q"], cwd=tmp)
        subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=tmp)
        subprocess.check_call(["git", "config", "user.name", "t"], cwd=tmp)
        f = tmp / "data" / "MFV_G8_state.json"
        f.parent.mkdir(parents=True)
        # Saturday 07:08 and 14:59
        for when, text in (("2026-11-21T07:08:06+00:00", "one"), ("2026-11-21T14:59:32+00:00", "two")):
            f.write_text(text, encoding="utf-8")
            subprocess.check_call(["git", "add", "data/MFV_G8_state.json"], cwd=tmp)
            env = os.environ.copy()
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
            subprocess.check_call(
                ["git", "commit", "-q", "-m", "chore: weekly metals fair value update 2026-11-21"],
                cwd=tmp, env=env,
            )
        alerts, info = {}, []
        W.check_duplicate_turn_publishes(tmp, utc(2026, 11, 21, 16, 0), alerts, info)
        self.assertIn("actions:metals:duplicate_turn", alerts)
        self.assertIn("mismo feed publicado dos veces en el mismo turno", alerts["actions:metals:duplicate_turn"])


if __name__ == "__main__":
    unittest.main()

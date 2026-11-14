"""Cost-only commit names, and commit-sha lookup scoped to the working repo.

Does not edit tests/test_cme_options_backfill_chunks.py. No network.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cme_options_backfill as B  # noqa: E402
import cme_options_collector as C  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "cme_options_backfill.yml")
DOWNLOAD = 'git commit -m "data: cme options backfill ${month} [skip netlify]"'
COST_ONLY = 'git commit -m "data: cme options backfill cost-only ${month} [skip netlify]"'

# Run 43606084284 (14-nov-2026, workflow_dispatch, dry_run). Plain download
# subjects, but every chunk in those commits has complete false.
RUN_43606084284 = frozenset((
    "bcf4d70edf4a8de1b3282a6de18ddee899099dde",
    "784cd8ed5deb93d78e5cd73c06e937391990fc7a",
    "77a218bf652231a7edb24dbd87568ea4ed178ee0",
    "745fe9d0c0e704a14a129ba8fb413df4a2acf8aa",
    "fb80703ee85ed203cd873c30a11d62fc2af2396a",
    "dce91b5db613dc07b9994da189d917828b2559dd",
    "f41b1d7f5f0952282cd24c43071e05ca6e5271b4",
    "e22b1b2f4a40737a9d555ef6177a039e1d213a38",
    "36cc8c96aca2c6c624f8f26508a9a64ac30d617c",
    "5640e135f06e2957680bb1e9df359a460275e9cb",
    "53e4d0a153632ae0cf40a318a03b55c36aa3a2e1",
    "d9c305688883241ebf0db842eeb89d4a9c0ac8bd",
    "53e816a6e4f40a1421c3e722b3850bfdec624087",
    "eae29c931dbb77009c5fb2ad8b536f47d8f25936",
    "5a242421c53170b96a24491a41268f7368c53458",
    "b0b5409051d878a205e801af6412a46953675497",
    "0bbc950ccdf13e1082e832b7df05ac6383441890",
    "363fdecb0f87593acf181575f923a8f16440a0c7",
    "f20296904102702dd2d61eef4afecb8bbd58dae7",
    "1e8391ceea301bc75ad5426b0efa165ef2410692",
    "3299cebd18426e4ee1b14d90ce6c947839bffa0b",
    "332ea4de303d1acab4351a74bdc95b28e593cdaa",
    "70e12f3e25a17dd896bdb7a46be84d2ff9dc2b31",
    "f10a81fb4092cdfdf896dc53a0c6c4162e36eb9b",
))


def _git(repo, *args):
    out = subprocess.run(
        ["git", "-C", repo, *args], check=False, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("%s\n%s" % (" ".join(args), out.stderr))
    return out.stdout.strip()


def _init_repo():
    repo = tempfile.mkdtemp()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "backfill-test@example.com")
    _git(repo, "config", "user.name", "backfill-test")
    return repo


def _commit_report(repo, subject, chunks, rev=None):
    folder = os.path.join(repo, "data", "options")
    os.makedirs(folder, exist_ok=True)
    body = {"chunks": chunks, "rev": rev if rev is not None else os.urandom(8).hex()}
    with open(os.path.join(folder, "backfill_report.json"), "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    _git(repo, "add", "data/options/backfill_report.json")
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _chunk(month, complete):
    return {"month": month, "complete": complete, "committed_sha": None}


def _header_http(calls):
    def http(method, url, headers, body):
        calls.append(url)
        endpoint = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
        if endpoint == "timeseries.get_range":
            kind = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["schema"][0]
            if kind == "statistics":
                header = ",".join(C.STATS_FINGERPRINT)
            else:
                header = "instrument_id,strike_price,expiration,instrument_class,symbol"
            return 200, {}, (header + "\n").encode("utf-8")
        if endpoint == "metadata.get_cost":
            return 200, {}, b"0.01"
        return 200, {}, b"1"
    return http


class CommitMessage(unittest.TestCase):
    def test_dry_run_commit_message_is_cost_only(self):
        with open(WF, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(COST_ONLY, text)
        self.assertIn(DOWNLOAD, text)
        dry = text.index('if [ "$mode" = "--dry-run" ]; then')
        cost = text.index(COST_ONLY)
        download = text.index(DOWNLOAD)
        self.assertLess(dry, cost)
        self.assertLess(cost, download)
        self.assertIn('mode="--dry-run"', text)
        self.assertIn('mode="--download"', text)


class LookupIgnoresCostOnly(unittest.TestCase):
    def setUp(self):
        self.repo = _init_repo()
        self.addCleanup(shutil.rmtree, self.repo)

    def test_lookup_skips_cost_only_and_incomplete_plain_commits(self):
        plain = _commit_report(
            self.repo, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", False)])
        download = _commit_report(
            self.repo, "data: cme options backfill 2024-08 [skip netlify]",
            [_chunk("2024-06", False), _chunk("2024-08", True)])
        cost_only = _commit_report(
            self.repo, "data: cme options backfill cost-only 2024-08 [skip netlify]",
            [_chunk("2024-08", True)])
        _commit_report(
            self.repo, "data: cme options backfill cost-only 2024-07 [skip netlify]",
            [_chunk("2024-07", True)])
        found = B.lookup_commit_shas(self.repo)
        self.assertEqual(found, {"2024-08": download})
        self.assertNotIn(plain, found.values())
        self.assertNotIn(cost_only, found.values())
        self.assertNotIn("2024-06", found)
        self.assertNotIn("2024-07", found)

    def test_newest_download_wins_and_incomplete_sha_is_cleared(self):
        older = _commit_report(
            self.repo, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", True)])
        newer = _commit_report(
            self.repo, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", True)])
        self.assertEqual(B.lookup_commit_shas(self.repo), {"2024-06": newer})
        self.assertNotEqual(older, newer)
        report = {"chunks": [
            {"month": "2024-06", "complete": False, "committed_sha": older},
            {"month": "2024-09", "complete": True, "committed_sha": "abc123"},
        ]}
        self.assertTrue(B.apply_commit_shas(report, repo=self.repo))
        self.assertEqual(report["chunks"][0]["committed_sha"], newer)
        self.assertEqual(report["chunks"][1]["committed_sha"], "abc123")

    def test_plain_incomplete_sha_is_dropped_when_the_repo_has_no_download(self):
        plain = _commit_report(
            self.repo, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", False)])
        report = {"chunks": [
            {"month": "2024-06", "complete": False, "committed_sha": plain},
            {"month": "2024-09", "complete": True, "committed_sha": "abc123"},
        ]}
        self.assertTrue(B.apply_commit_shas(report, repo=self.repo))
        self.assertIsNone(report["chunks"][0]["committed_sha"])
        self.assertEqual(report["chunks"][1]["committed_sha"], "abc123")
        self.assertEqual(B.lookup_commit_shas(self.repo), {})


class LookupScopedToWorkingRepo(unittest.TestCase):
    def setUp(self):
        self.here = _init_repo()
        self.there = _init_repo()
        self.addCleanup(shutil.rmtree, self.here)
        self.addCleanup(shutil.rmtree, self.there)

    def test_lookup_reads_the_given_repo_and_not_another(self):
        sha_here = _commit_report(
            self.here, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", True)])
        sha_there = _commit_report(
            self.there, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", True)])
        self.assertNotEqual(sha_here, sha_there)
        self.assertEqual(B.lookup_commit_shas(self.here), {"2024-06": sha_here})
        self.assertEqual(B.lookup_commit_shas(self.there), {"2024-06": sha_there})

    def test_directory_that_is_not_a_git_repo_returns_no_shas(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty)
        self.assertEqual(B.lookup_commit_shas(empty), {})
        missing = os.path.join(empty, "backfill_report.json")
        self.assertEqual(B.lookup_commit_shas(missing), {})
        report = {"chunks": [
            {"month": "2024-06", "complete": False, "committed_sha": "keep-me"},
        ]}
        self.assertFalse(B.apply_commit_shas(report, repo=empty))
        self.assertEqual(report["chunks"][0]["committed_sha"], "keep-me")

    def test_git_dir_in_the_environment_cannot_retarget_lookup(self):
        sha_here = _commit_report(
            self.here, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", True)])
        sha_there = _commit_report(
            self.there, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", True)])
        previous = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = os.path.join(self.there, ".git")
        try:
            found = B.lookup_commit_shas(self.here)
        finally:
            if previous is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = previous
        self.assertEqual(found, {"2024-06": sha_here})
        self.assertNotEqual(found["2024-06"], sha_there)

    def test_this_checkout_does_not_treat_run_43606084284_as_downloads(self):
        found = B.lookup_commit_shas(ROOT)
        self.assertTrue(RUN_43606084284.isdisjoint(found.values()))


class DownloadDoesNotKeepCostOnlySha(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "canonical")
        self.report_path = os.path.join(self.tmp, "backfill_report.json")
        os.makedirs(self.out)
        _git(self.tmp, "init", "-b", "main")
        _git(self.tmp, "config", "user.email", "backfill-test@example.com")
        _git(self.tmp, "config", "user.name", "backfill-test")

    def test_download_does_not_inherit_a_plain_cost_only_sha(self):
        cost_sha = _commit_report(
            self.tmp, "data: cme options backfill 2024-06 [skip netlify]",
            [_chunk("2024-06", False)])
        report = B.blank_report()
        report["chunks"] = [{
            "month": "2024-06", "complete": False, "get_cost": "0.66",
            "sessions_added": [], "skipped": [], "holidays": [],
            "committed_sha": cost_sha,
        }]
        with open(self.report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh)
        calls = []
        rc = B.main(
            ["--download", "--month", "2024-06"],
            http=_header_http(calls),
            env={"DATABENTO_API_KEY": "unit-test-key"},
            out_dir=self.out,
            report_path=self.report_path,
        )
        self.assertEqual(rc, 0)
        with open(self.report_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        chunk = saved["chunks"][0]
        self.assertEqual(chunk["month"], "2024-06")
        self.assertTrue(chunk["complete"])
        self.assertIsNone(chunk["committed_sha"])
        self.assertNotEqual(chunk["committed_sha"], cost_sha)
        self.assertTrue(any("timeseries.get_range" in url for url in calls))


if __name__ == "__main__":
    unittest.main()

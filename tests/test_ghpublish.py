"""Pruebas de g8common.ghpublish contra FakeGitHub (semántica documentada; NO equivale a la API real).

Demuestra: publicación todo-o-nada, conflicto (la rama se mueve), pérdida de titularidad de la reserva,
expiración, carrera en la ventana lectura→actualización, 401/403 y comprobación de credenciales sin
exponer el token."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

from g8common import g8http, ghpublish as G  # noqa: E402
from g8fakes import Clock, FakeGitHub  # noqa: E402

TOKEN = "ghp_TESTTOKEN_never_printed_123"


def mkrepo(gh, clock, token=TOKEN):
    return G.Repo("o", "r", token=token, api=gh.url, budget=g8http.Budget(10 ** 6, now=clock, env={}),
                  sleep=clock.sleep, now=clock)


def writer(paths, content_fn):
    return G.Build(paths, lambda cur, head: (content_fn(cur), {"n": len(paths)}))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gh = FakeGitHub({"data/A.csv": b"a0\n", "data/B.csv": b"b0\n", "data/other.csv": b"x\n"}, clock=self.clock)
        self.repo = mkrepo(self.gh, self.clock)

    def tearDown(self):
        self.gh.close()

    def test_family_published_in_one_commit(self):
        out = self.repo.publish(writer(["data/A.csv", "data/B.csv"], lambda c: {"data/A.csv": b"a1\n", "data/B.csv": b"b1\n"}), "m")
        self.assertEqual(out.status, G.PUBLISHED)
        f = self.gh.files()
        self.assertEqual((f["data/A.csv"], f["data/B.csv"], f["data/other.csv"]), (b"a1\n", b"b1\n", b"x\n"))
        self.assertEqual(len(self.gh.ref_log), 1)                      # un único avance de rama = publicación conjunta
        self.assertIsNotNone(out.observed_on_branch_utc)

    def test_noop_does_not_commit(self):
        out = self.repo.publish(writer(["data/A.csv"], lambda c: {}), "m")
        self.assertEqual(out.status, G.NOOP)
        self.assertEqual(self.gh.ref_log, [])

    def test_conflict_rebuilds_on_new_head_without_losing_other_writer(self):
        self.gh.before_patch = lambda gh: gh.direct_commit({"data/other.csv": b"x-actions\n"}, "actions")
        seen = []

        def build(cur):
            seen.append(cur["data/A.csv"])
            return {"data/A.csv": cur["data/A.csv"] + b"+mac\n"}
        out = self.repo.publish(writer(["data/A.csv"], build), "m")
        self.assertEqual(out.status, G.PUBLISHED)
        self.assertEqual(out.attempts, 2)                              # 1.º rechazado (no fast-forward), 2.º sobre la nueva cabeza
        f = self.gh.files()
        self.assertEqual(f["data/other.csv"], b"x-actions\n")         # el commit ajeno se conserva
        self.assertEqual(f["data/A.csv"], b"a0\n+mac\n")

    def test_conflict_exhausted(self):
        def always_move(gh):
            gh.direct_commit({"data/other.csv": ("x%d" % gh.seq).encode()})
            gh.before_patch = always_move
        self.gh.before_patch = always_move
        out = self.repo.publish(writer(["data/A.csv"], lambda c: {"data/A.csv": b"z"}), "m", max_attempts=3)
        self.assertEqual(out.status, G.CONFLICT_EXHAUSTED)
        self.assertEqual(self.gh.files()["data/A.csv"], b"a0\n")      # nada de la familia llegó

    def test_401_raises_auth(self):
        self.gh.fail_auth = True
        with self.assertRaises(G.GitHubError) as cm:
            self.repo.publish(writer(["data/A.csv"], lambda c: {"data/A.csv": b"z"}), "m")
        self.assertEqual(cm.exception.cls, g8http.FAIL_AUTH)
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_403_permission(self):
        self.gh.readonly_token = True
        with self.assertRaises(G.GitHubError) as cm:
            self.repo.publish(writer(["data/A.csv"], lambda c: {"data/A.csv": b"z"}), "m")
        self.assertEqual(cm.exception.cls, g8http.FAIL_PERMISSION)

    def test_rate_limit_waits_then_publishes(self):
        self.gh.rate_limit_once = 1
        out = self.repo.publish(writer(["data/A.csv"], lambda c: {"data/A.csv": b"z"}), "m")
        self.assertEqual(out.status, G.PUBLISHED)
        self.assertGreaterEqual(self.clock.slept[0], 30)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gh = FakeGitHub({"data/A.csv": b"a0\n"}, clock=self.clock)
        self.a = mkrepo(self.gh, self.clock)
        self.b = mkrepo(self.gh, self.clock)

    def tearDown(self):
        self.gh.close()

    def pub(self, repo, holder, epoch, val):
        return repo.publish(writer(["data/A.csv"], lambda c: {"data/A.csv": val}), "m", fence=("NZ-B2", holder, epoch),
                            lease_ttl_s=1200)

    def test_acquire_and_publish(self):
        st, rec = G.acquire_lease(self.a, "NZ-B2", "mac-primary")
        self.assertEqual((st, rec["epoch"]), ("ACQUIRED", 1))
        self.assertEqual(self.pub(self.a, "mac-primary", 1, b"A1").status, G.PUBLISHED)

    def test_second_executor_cannot_take_live_lease(self):
        G.acquire_lease(self.a, "NZ-B2", "mac-primary")
        st, rec = G.acquire_lease(self.b, "NZ-B2", "backup")
        self.assertEqual(st, "HELD_BY_OTHER")

    def test_expired_lease_taken_old_holder_cannot_publish(self):
        G.acquire_lease(self.a, "NZ-B2", "mac-primary", ttl_s=1200)
        self.clock.t += 1300                                             # caduca
        st, rec = G.acquire_lease(self.b, "NZ-B2", "backup")
        self.assertEqual((st, rec["epoch"]), ("ACQUIRED", 2))
        out = self.pub(self.a, "mac-primary", 1, b"STALE")              # el antiguo titular intenta publicar
        self.assertEqual(out.status, G.LOST_LEASE)
        self.assertEqual(self.gh.files()["data/A.csv"], b"a0\n")
        self.assertEqual(self.pub(self.b, "backup", 2, b"B").status, G.PUBLISHED)

    def test_own_expired_lease_refused_by_client(self):
        G.acquire_lease(self.a, "NZ-B2", "mac-primary", ttl_s=60)
        self.clock.t += 61
        out = self.pub(self.a, "mac-primary", 1, b"late")
        self.assertEqual(out.status, G.LOST_LEASE)                      # el cliente comprueba la caducidad con la hora del servidor
        self.assertIn("caducó", out.detail)

    def test_race_other_takes_lease_between_read_and_update(self):
        """La ventana que el servidor NO protege por sí mismo: A lee la cabeza con su reserva vigente; antes de
        su actualización de rama, la reserva caduca y B la toma. La actualización de A deja de ser
        fast-forward → rechazada; al releer, A ve epoch 2 → LOST_LEASE. Nada de A se publica."""
        G.acquire_lease(self.a, "NZ-B2", "mac-primary", ttl_s=1200)

        def b_takes(gh):
            self.clock.t += 1300
            gh.before_patch = None
            st, _ = G.acquire_lease(self.b, "NZ-B2", "backup")
            assert st == "ACQUIRED"
        self.gh.before_patch = b_takes
        out = self.pub(self.a, "mac-primary", 1, b"A-late")
        self.assertEqual(out.status, G.LOST_LEASE)
        self.assertEqual(self.gh.files()["data/A.csv"], b"a0\n")

    def test_expiry_in_window_without_takeover_is_accepted_and_documented(self):
        """Límite honesto: si la reserva caduca en la ventana y NADIE la toma, la rama acepta el commit
        (GitHub no conoce la reserva). El commit renueva la reserva del mismo titular; no hay tercero afectado."""
        G.acquire_lease(self.a, "NZ-B2", "mac-primary", ttl_s=1200)

        def expire_only(gh):
            self.clock.t += 1300
        self.gh.before_patch = expire_only
        out = self.pub(self.a, "mac-primary", 1, b"A1")
        self.assertEqual(out.status, G.PUBLISHED)
        lease = json.loads(self.gh.files()["data/_ingest/leases/NZ-B2.json"])
        self.assertEqual((lease["holder"], lease["epoch"]), ("mac-primary", 1))


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gh = FakeGitHub({}, clock=self.clock)

    def tearDown(self):
        self.gh.close()

    def test_classic_scopes_and_days_left(self):
        self.gh.scopes = "public_repo, workflow"
        from datetime import datetime, timedelta, timezone
        exp = datetime.fromtimestamp(self.clock(), tz=timezone.utc) + timedelta(days=7)
        self.gh.token_expiration = exp.strftime("%Y-%m-%d %H:%M:%S UTC")
        r = G.check_token(mkrepo(self.gh, self.clock), TOKEN)
        self.assertTrue(r["valid"])
        self.assertAlmostEqual(r["days_left"], 7.0, places=2)
        self.assertTrue(r["required_ok"])
        self.assertEqual(r["excess"], ["workflow"])
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_missing_public_repo(self):
        self.gh.scopes = "read:user"
        r = G.check_token(mkrepo(self.gh, self.clock), TOKEN)
        self.assertFalse(r["required_ok"])

    def test_fine_grained_no_scopes_header(self):
        self.gh.token_expiration = "2026-12-01 00:00:00 UTC"
        r = G.check_token(mkrepo(self.gh, self.clock, token="github_pat_x"), "github_pat_x")
        self.assertEqual(r["kind"], "fine-grained")
        self.assertIsNone(r["required_ok"])
        self.assertIn("latido", r["detail"])

    def test_revoked(self):
        self.gh.fail_auth = True
        r = G.check_token(mkrepo(self.gh, self.clock), TOKEN)
        self.assertFalse(r["valid"])
        self.assertEqual(r["cls"], g8http.FAIL_AUTH)


if __name__ == "__main__":
    unittest.main()

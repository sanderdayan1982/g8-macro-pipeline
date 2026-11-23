"""t354u (orden Sander 24-nov): metals pasa al VPS (timer g8-metals-weekly) y sale del dispatcher.

Sin red. Comprueba el espejo público del timer, el binario y que el workflow ya no tiene el cron semanal.
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VPS = os.path.join(ROOT, "vps")
WF = os.path.join(ROOT, ".github", "workflows", "metals_update.yml")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
sys.path.insert(0, VPS)


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


class MetalsWeeklyVps(unittest.TestCase):
    def test_timer_keeps_saturday_0700_utc(self):
        t = read(os.path.join(VPS, "g8-metals-weekly.timer"))
        self.assertIn("OnCalendar=Sat 07:00:00", t)
        self.assertIn("Unit=g8-metals-weekly.service", t)

    def test_unit_own_clone_lock_and_protecthome(self):
        u = read(os.path.join(VPS, "g8-metals-weekly.service"))
        self.assertIn("G8_METALS_WORKDIR=/srv/g8/prod/work/metals", u)
        self.assertIn("G8_METALS_LOCK=/srv/g8/prod/state/locks/metals.lock", u)
        self.assertIn("G8_METALS_FEED_LOCK=/srv/g8/prod/state/locks/metals-feed.lock", u)
        self.assertIn("ProtectHome=yes", u)
        self.assertIn("EnvironmentFile=/etc/g8/secrets.env", u)
        self.assertIn("--job metals", u)

    def test_workflow_has_no_weekly_cron_nor_push_trigger(self):
        y = read(WF)
        self.assertIn("workflow_dispatch:", y)
        self.assertNotIn("'0 7 * * 6'", y)
        self.assertNotRegex(y, r"^\s*push:\s*$")

    def test_weekly_slot_out_of_dispatch_schedule(self):
        with open(os.path.join(ROOT, "mac", "dispatch_schedule.json"), encoding="utf-8") as fh:
            ids = [s["id"] for s in json.load(fh)["slots"]]
        self.assertNotIn("metals", ids)

    def test_binary_owned_paths_and_vps_push_prefix(self):
        import metals_vps as M
        self.assertIn("data/MFV_G8_XAU.csv", M.COMPUTE_FILES)
        self.assertIn("data/alerts/state.json", M.PRIVATE_TRACKED)
        for p in M.COMPUTE_FILES + M.ALWAYS_FILES + M.VERIFY_FILES:
            self.assertTrue(p.startswith("data/"), p)
        self.assertNotIn("data/OPTIONS_SURFACE.json", M.COMPUTE_FILES + M.ALWAYS_FILES + M.VERIFY_FILES)
        src = read(os.path.join(VPS, "metals_vps.py"))
        self.assertEqual(re.findall(r'"\[vps-push\][^"]*"', src) and all("[skip netlify]" in m for m in re.findall(r'"\[vps-push\][^"]*"', src)), True)
        self.assertNotIn("--commit", re.sub(r"#.*", "", src))

    def test_missing_secrets_fail_by_name(self):
        import metals_vps as M
        saved = {k: os.environ.pop(k, None) for k in ("G8_FULL", "G8_DATA_READ_CI", "G8_DATA_WRITE")}
        try:
            with self.assertRaises(RuntimeError) as cm:
                M.required_secrets()
            self.assertIn("G8_DATA_READ_CI", str(cm.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_no_ip(self):
        for n in ("metals_vps.py", "g8-metals-weekly.service", "g8-metals-weekly.timer"):
            self.assertIsNone(IP_RE.search(read(os.path.join(VPS, n))), n)


class MetalsShadowVps(unittest.TestCase):
    """orden Sander 24-nov: migrado al VPS, sin cron ni turno en GitHub (metals_shadow, t354u)."""

    def test_timer_keeps_weekday_2245_utc(self):
        t = read(os.path.join(VPS, "g8-metals-shadow.timer"))
        self.assertIn("OnCalendar=Mon..Fri 22:45:00", t)
        self.assertIn("Unit=g8-metals-shadow.service", t)

    def test_unit_own_clone_and_lock_shared_feed_lock(self):
        u = read(os.path.join(VPS, "g8-metals-shadow.service"))
        self.assertIn("G8_METALS_WORKDIR=/srv/g8/prod/work/metals-shadow", u)
        self.assertIn("G8_METALS_LOCK=/srv/g8/prod/state/locks/metals-shadow.lock", u)
        self.assertIn("G8_METALS_FEED_LOCK=/srv/g8/prod/state/locks/metals-feed.lock", u)
        self.assertIn("ProtectHome=yes", u)
        self.assertIn("--job metals-shadow", u)
        self.assertIsNone(IP_RE.search(u))

    def test_workflow_dispatch_only(self):
        y = read(WF)
        self.assertIn("workflow_dispatch:", y)
        self.assertNotRegex(y, r"^\s*schedule:", )
        self.assertNotRegex(y, r"^\s*- cron:")

    def test_shadow_slot_and_exception_out(self):
        with open(os.path.join(ROOT, "mac", "dispatch_schedule.json"), encoding="utf-8") as fh:
            ids = [s["id"] for s in json.load(fh)["slots"]]
        self.assertNotIn("metals_shadow", ids)
        with open(os.path.join(ROOT, "mac", "dispatch_drift_exceptions.json"), encoding="utf-8") as fh:
            self.assertNotIn("metals_shadow", [e.get("id") for e in json.load(fh)["exceptions"]])


if __name__ == "__main__":
    unittest.main()

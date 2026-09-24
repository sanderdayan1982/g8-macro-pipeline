"""Lote 3B · descargadores del Mac (fetch_nzd_b2, fetch_chf_snb, fetch_tona_mac): original frente a nuevo.

Sin red: las respuestas se construyen con el formato de cada fuente (XLSX B2 del RBNZ generado con openpyxl a
partir de los CSV reales del repo; CSV del portal SNB; RSS del SNB; JSON del BoJ). El original usa su
`requests` (curl_cffi no disponible → misma alternativa que en un Mac sin curl_cffi); el nuevo, el transporte
de g8common con las mismas respuestas. Casos: equivalencia, 503 con reintentos y copia local intacta, respuesta
vacía, serie inválida (las demás se escriben), fuente caída (CHF: las otras siguen), interrupción atómica,
Retry-After persistido, recuperación y registro state/fetch_<clave>.json → latido → aviso con el motivo."""
import csv
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import fetcher_harness as H  # noqa: E402
from g8common import macfetch, series as S  # noqa: E402

NZ_IDS = {"INM.DB01.NZZV": "NZD_BILL_30D.csv", "INM.DB02.NZZV": "NZD_BILL_60D.csv", "INM.DB03.NZZV": "NZD_BILL_90D.csv",
          "INM.DG101.NZZCF": "NZD_BOND_1Y.csv", "INM.DG102.NZZCF": "NZD_BOND_2Y.csv", "INM.DG105.NZZCF": "NZD_BOND_5Y.csv",
          "INM.DG110.NZZCF": "NZD_BOND_10Y.csv", "INM.DN.NZK": "NZD_CASH_ON.csv"}
NZ_IIB = ["NZD_IIB_2035.csv", "NZD_IIB_2040.csv"]
CHF_T = {"1J": 1, "2J": 2, "3J": 3, "4J": 4, "5J": 5, "6J": 6, "7J": 7, "8J": 8, "9J": 9, "10J": 10, "20J": 20, "30J": 30}
_cnt = [0]


def dv_rows(fname, source=False):
    with open(os.path.join(ROOT, "data", fname), encoding="utf-8") as fh:
        rd = list(csv.DictReader(fh))
    return [(r["Date"], r["Value"]) + ((r.get("Source") or "",) if source else ()) for r in rd]


def nz_workbook(data, extra_date=None):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    cols = list(NZ_IDS.items()) + [("INM.DI%s.NZZC" % f[8:12], f) for f in NZ_IIB]
    ws.cell(1, 1, "Series")
    for j, (sid, f) in enumerate(cols, start=2):
        label = ("Inflation indexed bonds" if "IIB" in f else "Wholesale rates")
        ws.cell(1, j, label)
        ws.cell(2, j, ("20 September %s" % f[8:12]) if "IIB" in f else f[:-4])
        ws.cell(5, j, sid)
    look = {f: dict(data[f]) for _, f in cols}
    dates = sorted({d for f in look for d in look[f]})
    for i, d in enumerate(dates, start=6):
        ws.cell(i, 1, datetime.strptime(d, "%Y-%m-%d"))
        for j, (_, f) in enumerate(cols, start=2):
            if d in look[f]:
                ws.cell(i, j, float(look[f][d]))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def load(path, name):
    _cnt[0] += 1
    spec = importlib.util.spec_from_file_location("%s_%d" % (name, _cnt[0]), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MacBase(object):
    key = script = orig_path = new_path = None
    files = ()

    def setUp(self):
        self.roots = []
        self.orig = {f: open(os.path.join(ROOT, "data", f), "rb").read() for f in self.files}

    def tearDown(self):
        macfetch.TEST_TRANSPORT = macfetch.TEST_CLOCK = None
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def root(self):
        r = tempfile.mkdtemp()
        os.makedirs(os.path.join(r, "data"))
        for f, b in self.orig.items():
            with open(os.path.join(r, "data", f), "wb") as fh:
                fh.write(b)
        self.roots.append(r)
        return r

    def run_orig(self, serve, root, argv=()):
        fake = H._FakeReqMod(serve)
        with mock.patch.dict(sys.modules, {"requests": fake, "curl_cffi": None}):   # también `import requests` local
            mod = load(self.orig_path, self.script + "_orig")
            return self._orig_inner(mod, fake, root, argv)

    def _orig_inner(self, mod, fake, root, argv):
        mod.requests = fake
        if hasattr(mod, "crequests"):
            mod.crequests, mod._IMPERSONATE = None, False
        if hasattr(mod, "time"):
            mod.time = H._NoSleep(mod.time)
        self.adjust(mod, root)
        return self._main(mod, root, argv), mod

    def run_new(self, serve, root, argv=(), clock=None):
        calls = []

        def transport(method, url, headers, body, ct, rt, tt):
            calls.append(url)
            st, data = serve(url)
            hdrs = {}
            if isinstance(data, tuple):
                data, hdrs = data
            return st, hdrs, data
        clock = clock or H.clock_at_now()
        macfetch.TEST_TRANSPORT, macfetch.TEST_CLOCK = transport, clock
        try:
            mod = load(self.new_path, self.script + "_new")
            self.adjust(mod, root)
            rc = self._main(mod, root, argv)
        finally:
            macfetch.TEST_TRANSPORT = macfetch.TEST_CLOCK = None
        return rc, calls, clock

    def adjust(self, mod, root):
        pass

    def _main(self, mod, root, argv):
        old, cwd = sys.argv, os.getcwd()
        sys.argv = [self.script + ".py"] + list(argv)
        os.chdir(root)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = mod.main()
        except SystemExit as e:
            rc = e.code
        finally:
            sys.argv = old
            os.chdir(cwd)
        return rc

    def read(self, root):
        return {f: H.read(root, f) for f in self.files}

    def state(self, root):
        return json.load(open(os.path.join(root, "state", "fetch_%s.json" % self.key)))

    # ── comunes ─────────────────────────────────────────────────────────────
    def test_E1_equivalence(self):
        serve = self.serve(new_day=True)
        ro, rn = self.root(), self.root()
        rco, _ = self.run_orig(serve, ro)
        rcn, calls, _ = self.run_new(serve, rn)
        self.assertEqual((rco, rcn), (0, 0))
        o, n = self.read(ro), self.read(rn)
        for f in self.files:
            self.assertEqual(n[f], o[f], f)
        self.assertTrue([f for f in self.files if n[f] != self.orig[f]])
        st = self.state(rn)
        self.assertEqual((st["rc"], st["errors"]), (0, []))

    def test_F1_503_retried_files_intact_recorded(self):
        rn = self.root()
        rc, calls, clock = self.run_new(lambda u: (503, b"busy"), rn)
        self.assertEqual(rc, 1)
        self.assertEqual(self.read(rn), self.orig)
        self.assertEqual(clock.slept[:3], [10, 40, 90])
        st = self.state(rn)
        self.assertEqual(st["rc"], 1)
        self.assertTrue(st["errors"])
        self.assertIn("FAIL_TRANSIENT", json.dumps(st["requests"]))

    def test_F2_waf_403_not_retried(self):
        rn = self.root()
        rc, calls, clock = self.run_new(lambda u: (403, b"blocked"), rn)
        self.assertEqual(rc, 1)
        self.assertFalse(getattr(clock, "slept", []))
        self.assertEqual(self.read(rn), self.orig)

    def test_F3_empty_response(self):
        rn = self.root()
        rc, *_ = self.run_new(lambda u: (200, b""), rn)
        self.assertNotEqual(rc, 0)                     # 1 = descarga, 2 = formato (código del original)
        self.assertEqual(self.read(rn), self.orig)

    def test_F6_interruption_atomic(self):
        rn = self.root()
        real = os.replace
        seen = []

        def flaky(src, dst):
            if dst.endswith(".csv"):
                seen.append(dst)
                if len(seen) == min(2, len(self.files)):
                    raise KeyboardInterrupt("corte simulado")
            return real(src, dst)
        with mock.patch("g8common.series.os.replace", flaky):
            with self.assertRaises(KeyboardInterrupt):
                self.run_new(self.serve(new_day=True), rn)
        got = self.read(rn)
        for f in self.files:
            S.parse(got[f])                                                     # ninguna a medias
        self.assertEqual(got[os.path.basename(seen[-1])], self.orig[os.path.basename(seen[-1])])

    def test_F7_retry_after_persisted_and_recovery(self):
        rn = self.root()
        clock = H.clock_at_now()
        rc, calls, _ = self.run_new(lambda u: (503, (b"", {"retry-after": "3600"})), rn, clock=clock)
        self.assertEqual(rc, 1)
        nb = json.load(open(os.path.join(rn, "data", "_ingest", "not_before", "mac.json")))
        self.assertTrue(nb["not_before"])
        clock.t += 600
        rc2, calls2, _ = self.run_new(self.serve(new_day=True), rn, clock=clock)
        self.assertEqual(calls2, [])                                            # no se consulta antes de tiempo
        clock.t += 3600
        rc3, calls3, _ = self.run_new(self.serve(new_day=True), rn, clock=clock)
        self.assertEqual(rc3, 0)                                                # recuperación
        self.assertNotEqual(self.read(rn), self.orig)
        self.assertEqual(self.state(rn)["errors"], [])


class Test_fetch_nzd_b2(MacBase, unittest.TestCase):
    key, script = "nzd", "fetch_nzd_b2"
    orig_path = os.path.join(ROOT, "tests", "fixtures", "original", "fetch_nzd_b2.py")
    new_path = os.path.join(ROOT, "scripts", "fetch_nzd_b2.py")
    files = tuple(NZ_IDS.values()) + tuple(NZ_IIB)
    _cache = {}

    def serve(self, new_day=False, drop=None):
        k = (new_day, drop)
        if k not in self._cache:
            data = {f: dv_rows(f) for f in self.files}
            if new_day:
                for f in self.files:
                    data[f] = data[f] + [("2026-09-24", data[f][-1][1])]
            if drop:
                data[drop] = []
            cur = nz_workbook(data)
            hist = nz_workbook({f: [] for f in self.files})
            self._cache[k] = (cur, hist)
        cur, hist = self._cache[k]
        return lambda u: (200, hist if "1985-2017" in u else cur)

    def test_F4_one_invalid_series_others_written(self):
        rn = self.root()
        rc, *_ = self.run_new(self.serve(new_day=True, drop="NZD_BOND_5Y.csv"), rn)
        self.assertEqual(rc, 1)
        got = self.read(rn)
        self.assertEqual(got["NZD_BOND_5Y.csv"], self.orig["NZD_BOND_5Y.csv"])     # vacía: no se escribe
        self.assertIn(b"2026-09-24", got["NZD_BOND_10Y.csv"])                      # las demás, sí
        st = self.state(rn)
        self.assertEqual(st["files"]["NZD_BOND_5Y.csv"]["status"], "REJECTED")
        ro = self.root()
        self.run_orig(self.serve(new_day=True, drop="NZD_BOND_5Y.csv"), ro)
        self.assertEqual(H.read(ro, "NZD_BOND_5Y.csv"), b"Date,Value\n")         # el original la vaciaba

    def test_F5_history_splice_failure_keeps_local_history(self):
        data = {f: dv_rows(f) for f in self.files}
        cut = {f: [r for r in rows if r[0] >= "2020-01-01"] + [("2026-09-24", rows[-1][1])] for f, rows in data.items()}
        cur = nz_workbook(cut)
        rn = self.root()
        rc, *_ = self.run_new(lambda u: (503, b"") if "1985-2017" in u else (200, cur), rn, argv=())
        got = self.read(rn)
        for f in self.files:
            old = S.parse(self.orig[f])
            new = S.parse(got[f])
            self.assertTrue(set(old.rows) <= set(new.rows), f)                  # ninguna fecha anterior se pierde
            self.assertEqual(new.max_date, "20260924", f)


class Test_fetch_chf_snb(MacBase, unittest.TestCase):
    key, script = "chf", "fetch_chf_snb"
    orig_path = os.path.join(ROOT, "tests", "fixtures", "original", "fetch_chf_snb.py")
    new_path = os.path.join(ROOT, "scripts", "fetch_chf_snb.py")
    files = tuple("CHF_SPOT_%dY.csv" % y for y in CHF_T.values()) + ("CHF_NOM_10Y.csv", "CHF_SARON.csv")

    def serve(self, new_day=False, fail=None):
        curve = ['"CubeId";"rendeiduebd"', '"PublishingDate";"2026-09-01 14:30"', "", '"Date";"D0";"D1";"Value"']
        for t, y in CHF_T.items():
            rows = dv_rows("CHF_SPOT_%dY.csv" % y)
            for d, v in rows[-60:]:
                curve.append('"%s";"CHF";"%s";"%s"' % (d, t, v))
        sar = ['"CubeId";"zirepo"', '"PublishingDate";"2026-09-23 09:00"', "", '"Date";"D0";"Value"']
        for d, v, src in dv_rows("CHF_SARON.csv", source=True)[-40:]:
            if src == "zirepo":
                sar.append('"%s";"H0";"%s"' % (d, v))
        rss = "<rss>" + "".join("<item><title>CH: %s %s %s SNB</title></item>" % (v, c, d) for c, d, v in (
            ("R10", "2026-09-23", "0.575"), ("SARH", "2026-09-22", "-0.04"), ("SNBLZ", "2026-09-22", "0.00"))
            + ((("R10", "2026-09-24", "0.58"), ("SARH", "2026-09-23", "-0.05")) if new_day else ())) + "</rss>"
        bodies = {"rendeiduebd": "\n".join(curve).encode(), "zirepo": "\n".join(sar).encode(), "rss": rss.encode()}

        def serve(u):
            k = "rendeiduebd" if "rendeiduebd" in u else "zirepo" if "zirepo" in u else "rss"
            if fail == k:
                return 503, b"busy"
            return 200, bodies[k]
        return serve

    def test_F4_one_source_down_others_written(self):
        ro, rn = self.root(), self.root()
        rco, _ = self.run_orig(self.serve(new_day=True, fail="zirepo"), ro)
        rcn, *_ = self.run_new(self.serve(new_day=True, fail="zirepo"), rn)
        self.assertEqual((rco, rcn), (1, 1))
        self.assertIn(b"2026-09-24,0.58,rss", H.read(rn, "CHF_NOM_10Y.csv"))       # 10Y del RSS: sí
        self.assertNotIn(b"2026-09-24", H.read(ro, "CHF_NOM_10Y.csv"))             # el original abortaba todo
        self.assertIn(b"2026-09-23,-0.05,rss", H.read(rn, "CHF_SARON.csv"))        # SARON del RSS: sí
        self.assertIn("saron", " ".join(self.state(rn)["errors"]))


class Test_fetch_tona_mac(MacBase, unittest.TestCase):
    key, script = "tona", "fetch_tona_mac"
    orig_path = os.path.join(ROOT, "tests", "fixtures", "original", "fetch_tona_mac.py")
    new_path = os.path.join(ROOT, "mac", "fetch_tona_mac.py")
    files = ("TONA.csv",)

    def adjust(self, mod, root):
        mod.OUT = os.path.join(root, "data", "TONA.csv")
        mod.datetime = H.fixed_dt(H.NOW)

    def serve(self, new_day=False):
        rows = [r for r in H.repo_rows("TONA.csv") if r[0] >= "20210901"]
        if new_day:
            rows = rows + [("20260924", rows[-1][1])]
        b = H.boj_json(rows)
        return lambda u: (200, b)

    def test_F4_boj_status_error_in_json_is_transient(self):
        rn = self.root()
        body = json.dumps({"STATUS": 503, "MESSAGE": "DB error"}).encode()
        rc, calls, clock = self.run_new(lambda u: (200, body), rn)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 4)                                            # 503 dentro del JSON: se reintenta
        self.assertEqual(self.read(rn), self.orig)


class CurlTransport(unittest.TestCase):
    """El transporte del Mac conserva el orden original: curl_cffi Safari → Chrome124 → Chrome → requests."""

    def fake_modules(self, script):
        import types
        seen = []

        class R(object):
            def __init__(self, st):
                self.status_code, self.headers, self.content = st, {"Retry-After": "5"}, b"x"

        def creq(method, url, headers=None, data=None, timeout=None, impersonate=None):
            seen.append(impersonate)
            st = script.get(impersonate, 403)
            if isinstance(st, Exception):
                raise st
            return R(st)

        def req(method, url, headers=None, data=None, timeout=None):
            seen.append("requests")
            st = script.get("requests", 403)
            if isinstance(st, Exception):
                raise st
            return R(st)
        cc = types.ModuleType("curl_cffi")
        cc.requests = types.SimpleNamespace(request=creq)
        rq = types.ModuleType("requests")
        rq.request = req
        return {"curl_cffi": cc, "requests": rq}, seen

    def test_first_200_wins_in_original_order(self):
        mods, seen = self.fake_modules({"safari17_0": 403, "chrome124": 200})
        with mock.patch.dict(sys.modules, mods):
            t = macfetch.curl_transport(log=lambda *a: None)
            st, hdrs, body = t("GET", "https://x.invalid/", {}, None, 10, 30, 60)
        self.assertEqual((st, seen), (200, ["safari17_0", "chrome124"]))
        self.assertEqual(hdrs["retry-after"], "5")                              # cabeceras en minúscula para g8http

    def test_all_blocked_returns_last_status_for_classification(self):
        mods, seen = self.fake_modules({})
        with mock.patch.dict(sys.modules, mods):
            st, _, _ = macfetch.curl_transport(log=lambda *a: None)("GET", "https://x.invalid/", {}, None, 10, 30, 60)
        self.assertEqual(st, 403)
        self.assertEqual(seen, ["safari17_0", "chrome124", "chrome", "requests"])

    def test_network_errors_become_neterror(self):
        from g8common import g8http
        mods, seen = self.fake_modules({k: OSError("timed out") for k in ("safari17_0", "chrome124", "chrome", "requests")})
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(g8http.NetError) as cm:
                macfetch.curl_transport(log=lambda *a: None)("GET", "https://x.invalid/", {}, None, 10, 30, 60)
        self.assertEqual(cm.exception.kind, "timeout")


class HeartbeatDetail(unittest.TestCase):
    """state/fetch_<clave>.json → latido del publicador → aviso de Actions con el motivo."""

    def test_detail_reaches_watch_alert(self):
        import ingest_watch as W
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        os.makedirs(os.path.join(root, "sources"))
        open(os.path.join(root, "sources", "executors.csv"), "w").write(
            "executor_id,job,timezone,run_times_local,days,alert_after_min,active_from,recovery,provisional,justification\n"
            "mac-primary,nzchf-tona,Africa/Malabo,08:00,mon-sun,45,2026-09-20,x,Y,x\n")
        open(os.path.join(root, "sources", "credentials.csv"), "w").write("credential_id,expires_utc\n")
        p = os.path.join(root, "data", "_ingest", "latest", "mac-primary__nzchf-tona.json")
        os.makedirs(os.path.dirname(p))
        json.dump({"run_id": "r", "started_utc": "2026-09-24T07:00:05Z", "families": {}, "credentials": {"days_left": 90},
                   "fetch_status": {"chf": "1"},
                   "fetch_detail": {"chf": {"errors": ["saron: download failed: FAIL_TRANSIENT HTTP 503"]}}}, open(p, "w"))
        a, info = {}, []
        W.check_executors(root, datetime(2026, 9, 24, 8, 0).astimezone(), a, info)
        self.assertIn("saron: download failed", a["exec:mac-primary:nzchf-tona:fetch:chf"])

    def test_publisher_attaches_fetch_detail(self):
        import test_mac_push as TP
        t = TP.MacPushTests("test_no_news_heartbeat_only")
        t.setUp()
        try:
            os.makedirs(os.path.join(t.tmp, "state"), exist_ok=True)
            json.dump({"rc": 1, "errors": ["descarga B2: FAIL_ACCESS 403"], "files": {}, "requests": []},
                      open(os.path.join(t.tmp, "state", "fetch_nzd.json"), "w"))
            rc, rec, _ = t.run_push(t.fresh_fetch()[:1] + ["nzd=1,chf=0,tona=0"] + t.fresh_fetch()[2:])
            self.assertIn("FAIL_ACCESS", rec["fetch_detail"]["nzd"]["errors"][0])
            self.assertIn("FAIL_ACCESS", json.dumps(rec["alerts"]) + json.dumps(rec))
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()

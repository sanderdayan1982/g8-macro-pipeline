"""F3 — motor de frescura en paralelo (g8common/freshness.py, scripts/freshness_report.py): criterios A1–A5 de la
propuesta v3. Solo local: calendarios y reglas reales del repo, datos sintéticos o el historial de git (ed9ed64)."""
import csv
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
from g8common import freshness as FR  # noqa: E402
from g8common import ingest as ING, g8http  # noqa: E402
import freshness_report as FRR  # noqa: E402

UTC = timezone.utc
RULES, PARAMS, OUTPUTS, CALS = FR.load_config(ROOT)
OUT = {o["file"]: o for o in OUTPUTS}
BASE_REF = "ed9ed64"


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def D(s):
    return date.fromisoformat(s)


def ev(file, now, have, **kw):
    o = OUT[file]
    return FR.evaluate(o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS, now,
                       None if have is None else {D(x) for x in have}, **kw)


def has_git_ref():
    try:
        subprocess.run(["git", "-C", ROOT, "cat-file", "-e", BASE_REF + "^{commit}"], check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


# ── A4 · estados y márgenes ─────────────────────────────────────────────────────────────────────────────────────
class States(unittest.TestCase):
    def test_timed_due_overdue_stale_transitions(self):
        # €STR: obs del 21-sep se publica el 22-sep 08:00 CEST (06:00Z); G = 60 min
        self.assertEqual(ev("ESTR.csv", T("2026-09-22T05:59Z"), ["2026-09-18"])["state"], "CURRENT")
        r = ev("ESTR.csv", T("2026-09-22T06:30Z"), ["2026-09-18"])
        self.assertEqual((r["state"], r["due_obs"], r["overdue_at"]), ("DUE", "2026-09-21", "2026-09-22T07:00:00Z"))
        r = ev("ESTR.csv", T("2026-09-22T07:00Z"), ["2026-09-18"])
        self.assertEqual((r["state"], r["missing"]), ("OVERDUE", ["2026-09-21"]))
        r = ev("ESTR.csv", T("2026-09-23T07:00Z"), ["2026-09-18"])
        self.assertEqual((r["state"], r["missing"]), ("STALE", ["2026-09-21", "2026-09-22"]))
        self.assertEqual(ev("ESTR.csv", T("2026-09-23T07:00Z"), ["2026-09-22"])["state"], "CURRENT")

    def test_g_never_makes_missing_data_current(self):
        for m in range(0, 24 * 60, 15):
            now = T("2026-09-22T00:00Z") + timedelta(minutes=m)
            r = ev("ESTR.csv", now, ["2026-09-17"])            # falta el 18 (exigible desde el 21 07:00Z)
            self.assertIn(r["state"], FR.LATE_STATES, now)

    def test_holiday_does_not_hide_previous_gap(self):
        # 25-dic-2026 festivo TARGET: si ya faltaba la obs del 23-dic, sigue OVERDUE durante el festivo
        r = ev("ESTR.csv", T("2026-12-25T12:00Z"), ["2026-12-22"])
        self.assertEqual((r["state"], r["missing"]), ("OVERDUE", ["2026-12-23"]))
        self.assertEqual(ev("ESTR.csv", T("2026-12-25T12:00Z"), ["2026-12-23"])["state"], "NO_PUBLICATION")

    def test_unknown_hour_pending_then_overdue_at_end_of_local_day_no_extra_day(self):
        # bills EE. UU.: obs del 21-sep se publica el 22-sep a hora desconocida (Nueva York)
        r = ev("US_BILL_3M.csv", T("2026-09-22T20:00Z"), ["2026-09-18"])
        self.assertEqual((r["state"], r["due_obs"]), ("PENDING_TIME_UNKNOWN", "2026-09-21"))
        self.assertEqual(ev("US_BILL_3M.csv", T("2026-09-23T03:59Z"), ["2026-09-18"])["state"], "PENDING_TIME_UNKNOWN")
        r = ev("US_BILL_3M.csv", T("2026-09-23T04:00Z"), ["2026-09-18"])   # medianoche de Nueva York del 23
        self.assertEqual((r["state"], r["missing"], r["overdue_since"]), ("OVERDUE", ["2026-09-21"], "2026-09-23T04:00:00Z"))

    def test_unknown_schedule_not_monitored_absent(self):
        r = ev("GB_POLICY.csv", T("2026-09-22T12:00Z"), ["2026-09-14"])
        self.assertEqual(r["state"], "UNKNOWN_SCHEDULE")
        self.assertEqual(r["age_days"], 8)                           # el hecho se muestra, no se oculta
        self.assertEqual(ev("S01B.json", T("2026-09-22T12:00Z"), ["2026-09-22"])["state"], "NOT_MONITORED")
        self.assertEqual(ev("SOFR.csv", T("2026-09-22T12:00Z"), None)["state"], "NO_OBS_DATE")
        self.assertEqual(ev("NZD_CASH_ON.csv", T("2026-09-22T12:00Z"), ["2026-09-11"])["state"], "UNKNOWN_SCHEDULE")

    def test_facts_axis_is_kept_whatever_the_state(self):
        facts = {"status": "FAIL", "errors": ["HTTP 503"]}
        r = ev("ESTR.csv", T("2026-09-22T05:00Z"), ["2026-09-18"], facts=facts)
        self.assertEqual((r["state"], r["facts"]), ("CURRENT", facts))

    def test_provisional_rule_is_flagged(self):
        r = ev("SONIA.csv", T("2026-09-23T12:00Z"), ["2026-09-18"])
        self.assertIn("RULE_PROVISIONAL", r["flags"])
        self.assertNotIn("RULE_PROVISIONAL", ev("ESTR.csv", T("2026-09-23T12:00Z"), ["2026-09-18"])["flags"])



# ── C3-1 · la causa sale de lo EJECUTADO, nunca de un cron programado ─────────────────────────────────────────
class Cause(unittest.TestCase):
    """ESTR: la obs del 21-sep se publica el 22-sep 06:00Z; la única pasada activa del grupo es la FINAL (21:30Z)."""
    NOW = T("2026-09-22T22:00Z")                                     # la FINAL ya estaba programada (21:30Z)

    def cause(self, runs=None, downloads=None, now=None, evidence_available=True):
        r = ev("ESTR.csv", now or self.NOW, ["2026-09-18"], passes=FR.SC.load_passes(ROOT), runs=runs or [],
               evidence={"ESTR.csv": downloads or []}, evidence_available=evidence_available)
        return r["cause"], r["cause_certainty"], r

    def test_scheduled_cron_without_recorded_run_is_uncertain_not_a_query(self):
        c, cert, r = self.cause()
        self.assertEqual((c, cert), ("PROGRAMADA_SIN_EJECUCION_REGISTRADA", "incierta"))
        self.assertEqual(r["cause_detail"]["scheduled_passes"], [("FINAL", "2026-09-22T21:30Z")])
        self.assertEqual(r["cause_detail"]["executed_runs"], [])
        self.assertFalse([f for f in r["flags"] if "CONSULTADA" in f])   # nunca «consultada» sin ejecución

    def test_no_scheduled_pass_is_a_configuration_fact(self):
        c, cert, _ = self.cause(now=T("2026-09-22T12:00Z"))
        self.assertEqual((c, cert), ("SIN_PASADA_PROGRAMADA", "configuracion"))

    def test_delayed_cron_that_ran_later_without_novelty(self):
        run = {"source": "actions", "run_id": "r1", "started_utc": "2026-09-22T23:40:00Z", "rc": 0,
               "requests": [{"cls": "OK"}], "files": {"ESTR.csv": {"status": "NOOP", "src_max": "2026-09-18"}}}
        c, cert, _ = self.cause(runs=[run], now=T("2026-09-22T23:50Z"))
        self.assertEqual((c, cert), ("CONSULTADA_SIN_NOVEDAD", "registrada"))

    def test_omitted_cron_before_run_then_http_failure(self):
        run = {"source": "actions", "run_id": "r2", "started_utc": "2026-09-22T21:45:00Z", "rc": 1,
               "requests": [{"cls": "FAIL_TRANSIENT", "status": 503}], "files": {}}
        c, cert, _ = self.cause(runs=[run])
        self.assertEqual((c, cert), ("EJECUTADA_CON_FALLO", "registrada"))

    def test_successful_download_without_the_date(self):
        dl = [{"ok": True, "query_utc": "2026-09-22T21:46:00Z", "src_max": "2026-09-18", "obs": []}]
        self.assertEqual(self.cause(downloads=dl)[0], "CONSULTADA_SIN_NOVEDAD")

    def test_new_download_held_is_received_not_published(self):
        dl = [{"ok": True, "query_utc": "2026-09-22T21:46:00Z", "src_max": "2026-09-21", "wrote": False,
               "obs": [{"date": "2026-09-21", "kind": "new", "accepted": False}]}]
        self.assertEqual(self.cause(downloads=dl)[0], "RECIBIDA_RETENIDA")

    def test_run_before_publication_does_not_count(self):
        run = {"source": "actions", "run_id": "r0", "started_utc": "2026-09-22T05:00:00Z", "rc": 0, "files": {}}
        self.assertEqual(self.cause(runs=[run])[0], "PROGRAMADA_SIN_EJECUCION_REGISTRADA")

    def test_historical_replay_declares_missing_evidence(self):
        _, cert, r = self.cause(evidence_available=False)
        self.assertEqual(cert, "incierta")
        self.assertIn("EVIDENCIA_NO_DISPONIBLE", r["flags"])

    def test_end_to_end_real_run_ledger_from_ingest_finish(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        shutil.copytree(os.path.join(ROOT, "sources"), os.path.join(root, "sources"))
        os.makedirs(os.path.join(root, "data"))
        with open(os.path.join(root, "data", "ESTR.csv"), "w") as fh:
            fh.write("DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260918,1,1,1,1,0\n")
        c = ING.Ingest("fetch_estr", root=root, now=lambda: T("2026-09-22T21:40Z").timestamp())
        c.requests_log.append({"cls": "FAIL_TRANSIENT", "status": 503, "url": "https://x.invalid"})
        c.fail("HTTP 503 agotado")
        c.finish(1)
        src = FRR.TreeSource(root)
        runs = src.runs_for(OUT["ESTR.csv"])
        self.assertEqual([(x["source"], x["rc"]) for x in runs], [("actions", 1)])   # puntero + registro, sin duplicar
        r = ev("ESTR.csv", T("2026-09-22T22:00Z"), ["2026-09-18"], passes=FR.SC.load_passes(ROOT), runs=runs)
        self.assertEqual(r["cause"], "EJECUTADA_CON_FALLO")


# ── C3-2 · hechos de Actions (también sin publicación), g8step y latido del Mac ──────────────────────────────
class Facts(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        shutil.copytree(os.path.join(ROOT, "sources"), os.path.join(self.root, "sources"))
        os.makedirs(os.path.join(self.root, "data", "_ingest", "latest"))

    def ingest(self, job, at, fail=None, publish=None):
        c = ING.Ingest(job, root=self.root, now=lambda: T(at).timestamp())
        c.requests_log.append({"cls": "FAIL_TRANSIENT" if fail else "OK", "status": 503 if fail else 200,
                               "url": "https://x.invalid"})
        for f, data in (publish or {}).items():
            c.publish(f, data)
        if fail:
            c.fail(fail)
        c.finish(1 if fail else 0)

    def facts(self, f):
        return FRR.TreeSource(self.root).facts_for(OUT[f])

    def test_failure_before_publishing_is_kept(self):
        self.ingest("fetch_sofr", "2026-09-24T12:00Z", fail="HTTP 503 agotado")
        fx = self.facts("SOFR.csv")
        self.assertTrue(fx["available"])
        self.assertEqual((fx["latest"]["rc"], fx["latest"]["requests"][0]["cls"], fx["latest"]["file"]["status"]),
                         (1, "FAIL_TRANSIENT", "SIN_RESULTADO_PARA_ESTE_FICHERO"))
        self.assertTrue(fx["latest"]["errors"])

    def test_partial_failure_then_recovery(self):
        body = b"DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n20260923,4,4,4,4,0\n"
        self.ingest("fetch_floor_spreads", "2026-09-24T12:00Z", fail="FLOOR_CAD: HTTP 503",
                    publish={"FLOOR_USD.csv": body})
        self.assertEqual(self.facts("FLOOR_USD.csv")["latest"]["file"]["status"], "PUBLISH")
        self.assertEqual(self.facts("FLOOR_CAD.csv")["latest"]["file"]["status"], "SIN_RESULTADO_PARA_ESTE_FICHERO")
        self.assertEqual(self.facts("FLOOR_CAD.csv")["latest"]["rc"], 1)
        self.ingest("fetch_floor_spreads", "2026-09-24T21:40Z", publish={"FLOOR_CAD.csv": body})
        fx = self.facts("FLOOR_CAD.csv")
        self.assertEqual((fx["latest"]["rc"], fx["latest"]["file"]["status"]), (0, "PUBLISH"))
        runs = FRR.TreeSource(self.root).runs_for(OUT["FLOOR_CAD.csv"])
        self.assertEqual([x["rc"] for x in runs], [1, 0])                 # el historial conserva el fallo

    def mac(self, name, finished, fam_files, fetch_rc, errors):
        rec = {"run_id": "mac-" + finished, "executor": "mac-primary", "job": "nzchf-tona",
               "started_utc": finished, "finished_utc": finished,
               "families": {"NZ-B2": {"status": "HELD", "files": fam_files}},
               "fetch_detail": {"nzd": {"rc": fetch_rc, "errors": errors, "files": {}}}, "fetch_status": {"nzd": str(fetch_rc)}}
        with open(os.path.join(self.root, "data", "_ingest", "latest", name), "w") as fh:
            json.dump(rec, fh)

    def test_mac_heartbeat_families_and_fetch_detail(self):
        self.mac("mac-primary__nzchf-tona.json", "2026-09-24T07:01:00Z",
                 {"NZD_BOND_10Y.csv": {"status": "HELD", "held": [{"date": "20260923"}], "src_max": "20260923"}},
                 1, ["HTTP 503 agotado"])
        fx = self.facts("NZD_BOND_10Y.csv")
        self.assertEqual((fx["latest_source"], fx["latest"]["file"]["status"], fx["latest"]["file"]["held"]),
                         ("mac:NZ-B2/nzd", "HELD", 1))
        self.assertEqual((fx["latest"]["rc"], fx["latest"]["errors"]), (1, ["HTTP 503 agotado"]))

    def test_several_pointers_resolved_by_time_not_name(self):
        # TONA la escriben Actions y el Mac: manda el registro más reciente, con procedencia de ambos
        self.ingest("fetch_tona", "2026-09-24T21:40Z", fail="HTTP 403")
        rec = {"run_id": "mac-x", "executor": "a-mac", "job": "nzchf-tona", "started_utc": "2026-09-24T07:00:00Z",
               "finished_utc": "2026-09-24T07:01:00Z",
               "families": {"JP-TONA": {"status": "PUBLISHED", "published": True,
                                        "files": {"TONA.csv": {"status": "PUBLISH", "src_max": "20260924"}}}},
               "fetch_detail": {"tona": {"rc": 0}}, "fetch_status": {"tona": "0"}}
        with open(os.path.join(self.root, "data", "_ingest", "latest", "a-mac__nzchf-tona.json"), "w") as fh:
            json.dump(rec, fh)                                          # nombre alfabéticamente anterior
        fx = self.facts("TONA.csv")
        self.assertEqual(fx["latest_source"], "actions:fetch_tona")
        self.assertEqual(set(fx["by_source"]), {"actions:fetch_tona", "mac:JP-TONA/tona"})

    def test_step_ledger_timeout_is_a_fact(self):
        doc = {"job": "daily", "executor": "actions", "run_id": "999", "written_utc": "2026-09-24T21:50:00Z",
               "steps": [{"name": "real_yields", "status": "TIMEOUT", "rc": 124, "started_utc": "2026-09-24T21:40:00Z"}]}
        with open(os.path.join(self.root, "data", "_ingest", "latest", "actions__job_daily.json"), "w") as fh:
            json.dump(doc, fh)
        fx = self.facts("RY_G8_USD.csv")
        self.assertEqual((fx["latest"]["source"], fx["latest"]["status"]), ("step", "TIMEOUT"))

    def test_outputs_without_execution_record_say_so(self):
        fx = self.facts("USD_FACTOR.json")
        self.assertEqual(fx["available"], False)
        self.assertIn("sin registro de ejecución", fx["reason"])
        for o in OUTPUTS:                                              # toda salida de Actions/Mac tiene su fuente
            if o["rule_id"] in ("SOFR", "ESTR", "SONIA", "TONA", "CORRA", "AONIA", "NZ_B2", "CH_R10", "CH_SARON",
                                "CH_CURVE", "US_BILLS", "EUR_CURVE", "GBP_CURVE", "JPY_CURVE", "AUD_F1", "FLOORS",
                                "BIS_POLICY", "ACM_TP", "EUR_RY", "JPY_RY"):
                self.assertTrue(o["sources"], o["file"])


# ── A1/A2 · salidas agrupadas y derivados ─────────────────────────────────────────────────────────────────────────
class GroupedAndDerived(unittest.TestCase):
    def test_floors_each_output_uses_its_own_calendar(self):
        # 12-oct-2026: festivo en EE. UU. y Canadá, NO en TARGET
        now = T("2026-10-14T01:00Z")
        eur = ev("FLOOR_EUR.csv", now, ["2026-10-09"])
        usd = ev("FLOOR_USD.csv", now, ["2026-10-09"])
        self.assertEqual((eur["state"], eur["missing"]), ("OVERDUE", ["2026-10-12"]))
        self.assertNotIn(usd["state"], FR.LATE_STATES)                 # el festivo de EE. UU. no se hereda…
        self.assertEqual((OUT["FLOOR_USD.csv"]["calendar"], OUT["FLOOR_EUR.csv"]["calendar"],
                          OUT["FLOOR_CAD.csv"]["calendar"]), ("US", "TARGET", "CA"))   # …ni al revés

    def _derived(self, have, driver_max, evidence=None):
        return ev("ACM_G8_NZD.csv", T("2026-09-24T12:00Z"), have,
                  input_max_at=lambda f, t: D(driver_max), evidence=evidence)

    def test_derived_behind_inputs_and_version_uncertainty(self):
        r = self._derived(["2026-09-22"], "2026-09-23")
        self.assertEqual((r["state"], r["missing"]), ("BEHIND_INPUTS", ["2026-09-23"]))
        r = self._derived(["2026-09-23"], "2026-09-23")
        self.assertEqual(r["state"], "CURRENT")
        self.assertIn("INPUT_VERSION_UNKNOWN", r["flags"])            # nunca «al día» sin declarar la incertidumbre

    # C3-3 · observada ≠ aceptada ≠ publicada ≠ consumida
    REV = {"query_utc": "2026-09-24T07:00:00Z", "ok": True, "wrote": True,
           "obs": [{"date": "2026-09-23", "kind": "revision", "accepted": True, "version": "b"}]}

    def derived(self, rev, written_at=None, out_records=None):
        evd = {"NZD_BOND_10Y.csv": [rev]}
        if out_records is not None:
            evd["ACM_G8_NZD.csv"] = out_records
        return ev("ACM_G8_NZD.csv", T("2026-09-24T12:00Z"), ["2026-09-23"],
                  input_max_at=lambda f, t: D("2026-09-23"), evidence=evd, written_at=written_at)

    def test_published_revision_after_write_is_not_current_nor_agreement(self):
        r = self.derived(self.REV, written_at=T("2026-09-23T22:00Z"))
        self.assertEqual((r["state"], r["revision_order"]), ("INPUT_REVISED", "posterior"))
        self.assertIn("INPUTS_REVISED_AFTER", r["flags"])
        cmp_ = FRR.compare({"outputs": [r], "at_utc": "x"}, {"ACM_G8_NZD.csv": [("NZD_ACM", "LIVE")]})
        self.assertEqual(cmp_["rows"][0]["kind"], "NUEVO_MAS_ESTRICTO")

    def test_write_time_from_output_evidence_when_git_is_missing(self):
        r = self.derived(self.REV, out_records=[{"query_utc": "2026-09-23T22:00:00Z", "wrote": True}])
        self.assertEqual((r["state"], r["written_at_source"]), ("INPUT_REVISED", "evidencia de la salida"))

    def test_held_revision_is_visible_but_does_not_change_the_input(self):
        rev = dict(self.REV, wrote=False, obs=[dict(self.REV["obs"][0], accepted=False)])
        r = self.derived(rev, written_at=T("2026-09-23T22:00Z"))
        self.assertEqual(r["state"], "CURRENT")
        self.assertIn("INPUT_REVISION_HELD", r["flags"])
        self.assertNotIn("INPUTS_REVISED_AFTER", r["flags"])
        self.assertEqual(r["held_input_revisions"][0]["date"], "2026-09-23")

    def test_unknown_write_time_is_declared_not_assumed(self):
        r = self.derived(self.REV)
        self.assertEqual((r["state"], r["revision_order"]), ("INPUT_REVISION_UNRESOLVED", "desconocido"))
        self.assertIn("INPUT_REVISION_ORDER_UNKNOWN", r["flags"])

    def test_output_recalculated_after_revision_closes_the_state(self):
        r = self.derived(self.REV, written_at=T("2026-09-24T21:50Z"))
        self.assertEqual((r["state"], r["revisions_consumed"]), ("CURRENT", 1))

    def test_compact_evidence_is_declared_incomplete(self):
        rev = {"query_utc": "2026-09-24T07:00:00Z", "ok": True, "wrote": True, "obs": [], "obs_complete": False,
               "obs_ranges": [{"kind": "revision", "from": "2026-01-02", "to": "2026-09-23", "n": 2500}]}
        r = self.derived(rev, written_at=T("2026-09-23T22:00Z"))
        self.assertEqual(r["state"], "INPUT_REVISED")
        self.assertIn("EVIDENCE_INCOMPLETE", r["flags"])

    def test_derived_uses_driving_inputs(self):
        o = OUT["ACM_G8_CHF.csv"]
        self.assertEqual(o["drivers"], "CHF_NOM_10Y.csv")              # la curva mensual no fija la fecha exigible
        seen = []

        def ima(f, t):
            seen.append(f)
            return D("2026-09-23") if f == "CHF_NOM_10Y.csv" else D("2026-08-31")
        r = ev("ACM_G8_CHF.csv", T("2026-09-24T12:00Z"), ["2026-09-22"], input_max_at=ima)
        self.assertEqual((r["state"], set(seen)), ("BEHIND_INPUTS", {"CHF_NOM_10Y.csv"}))


# ── A5 · T23 cambios de hora por zona IANA y cruces de día ──────────────────────────────────────────────────────
class DST(unittest.TestCase):
    ZONES = ["Pacific/Auckland", "Australia/Sydney", "Europe/Berlin", "Europe/London", "Europe/Zurich",
             "America/New_York", "America/Toronto", "Asia/Tokyo"]

    @staticmethod
    def transitions(zone, year=2026):
        tz, out = ZoneInfo(zone), []
        t = datetime(year, 1, 1, tzinfo=UTC)
        prev = t.astimezone(tz).utcoffset()
        while t.year == year:
            t += timedelta(hours=1)
            o = t.astimezone(tz).utcoffset()
            if o != prev:
                out.append((t.astimezone(tz).date(), prev, o))
                prev = o
        return out

    def test_transition_dates_come_from_zoneinfo(self):
        nz = [d for d, _, _ in self.transitions("Pacific/Auckland")]
        self.assertEqual(nz, [date(2026, 4, 5), date(2026, 9, 27)])      # NZ: septiembre, no octubre
        self.assertEqual([d for d, _, _ in self.transitions("Australia/Sydney")], [date(2026, 4, 5), date(2026, 10, 4)])
        self.assertEqual(self.transitions("Asia/Tokyo"), [])

    def test_due_keeps_local_hour_across_every_transition(self):
        for zone in self.ZONES:
            for day, before, after in self.transitions(zone):
                rule = {"rule_id": "X", "calendar": "", "timezone": zone, "pub_local": "09:00", "pub_offset_bd": "0",
                        "frequency": "daily", "status": "PROVISIONAL"}
                param = {"model": "bd_offset", "g_min": "60"}
                a = day - timedelta(days=7)
                while a.weekday() >= 5:
                    a -= timedelta(days=1)
                b = day + timedelta(days=7)
                while b.weekday() >= 5:
                    b += timedelta(days=1)
                due = {}
                for d in (a, b):
                    pubs = FR.publications(rule, param, "", zone, CALS, datetime.combine(
                        d + timedelta(days=1), datetime.min.time(), tzinfo=ZoneInfo(zone)).astimezone(UTC))
                    due[d] = [x for x in pubs if x[0] == d][0][1]
                    self.assertEqual(due[d].astimezone(ZoneInfo(zone)).strftime("%H:%M"), "09:00", (zone, d))
                # la hora UTC se desplaza exactamente lo que cambia el desfase de la zona
                mins = lambda t: t.hour * 60 + t.minute                                   # noqa: E731
                self.assertEqual((mins(due[a]) - mins(due[b])) % 1440,
                                 int((after - before).total_seconds() // 60) % 1440, (zone, day))
                self.assertNotEqual(mins(due[a]), mins(due[b]), (zone, day))

    def test_utc_day_crossing_tona(self):
        pubs = FR.publications(RULES["TONA"], PARAMS["TONA"], "JP", "Asia/Tokyo", CALS, T("2026-09-24T00:30Z"))
        d, due, exig = pubs[0]
        self.assertEqual((d, due), (date(2026, 9, 17), datetime(2026, 9, 23, 23, 50, tzinfo=UTC)))   # 08:50 JST del 24


# ── A5 · T24 y ciclos semanales/mensuales durante un año ─────────────────────────────────────────────────────────
class Cycles(unittest.TestCase):
    def test_weekly_publication_on_holiday_moves_to_next_business_day(self):
        # vie 25-dic-2026 y lun 28 (Boxing Day observado) festivos en AU → la tabla semanal F2 se espera el martes
        # 29 a las 14:00 de Sídney (supuesto provisional documentado); los datos siguen anclados al viernes NOMINAL:
        # hasta el miércoles 23 (no el 24, que daría falsos atrasos)
        pubs = FR.publications(RULES["AUD_F2"], PARAMS["AUD_F2"], "AU", "Australia/Sydney", CALS, T("2026-12-29T06:00Z"))
        d, due, _ = pubs[0]
        self.assertEqual(due.astimezone(ZoneInfo("Australia/Sydney")).strftime("%Y-%m-%d %H:%M"), "2026-12-29 14:00")
        self.assertEqual(d, date(2026, 12, 23))
        dues = [x[1].astimezone(ZoneInfo("Australia/Sydney")).date() for x in pubs]
        self.assertNotIn(date(2026, 12, 25), dues)
        self.assertNotIn(date(2026, 12, 28), dues)

    def test_ch_curve_first_business_day_is_holiday(self):
        pubs = FR.publications(RULES["CH_CURVE"], PARAMS["CH_CURVE"], "CH", "Europe/Zurich", CALS, T("2027-01-05T00:00Z"))
        self.assertEqual(pubs[0][:2], (date(2026, 12, 30), datetime(2027, 1, 4, 13, 30, tzinfo=UTC)))
        # el 1-ene (festivo) no se espera nada nuevo: con noviembre publicado no hay atraso
        self.assertNotIn(ev("CHF_SPOT_1Y.csv", T("2027-01-01T18:00Z"), ["2026-11-30"])["state"], FR.LATE_STATES)

    def _simulate(self, file, start, end, skip=None):
        """Fuente puntual: cada publicación llega exactamente a su hora esperada (salvo `skip`, que nunca llega)."""
        o = OUT[file]
        rule, param = RULES[o["rule_id"]], PARAMS[o["rule_id"]]
        cal, tz = o["calendar"] or rule["calendar"], o["timezone"] or rule["timezone"]
        states, allpubs, t = [], set(), start
        while t <= end:
            pubs = FR.publications(rule, param, cal, tz, CALS, t)
            allpubs.update(pubs)
            arrived = [d for d, due, _ in pubs if due <= t and d != skip]
            have = {max(arrived)} if arrived else {date(2000, 1, 1)}
            states.append((t, FR.evaluate(o, rule, param, CALS, t, have)))
            t += timedelta(hours=6)
        return states, sorted(allpubs, key=lambda x: x[1])

    def test_year_of_on_time_publications_never_late(self):
        for f in ("CHF_SPOT_1Y.csv", "RY_G8_AUD.csv", "pos_g8_cot.json", "ESTR.csv", "US_BILL_3M.csv"):
            states, _ = self._simulate(f, T("2026-10-01T00:00Z"), T("2027-09-30T00:00Z"))
            late = [(t, r["state"]) for t, r in states if r["state"] in FR.LATE_STATES]
            self.assertEqual(late, [], f)

    def test_one_missing_publication_overdue_then_stale(self):
        for f in ("CHF_SPOT_1Y.csv", "RY_G8_AUD.csv"):
            _, pubs = self._simulate(f, T("2027-03-01T00:00Z"), T("2027-03-31T00:00Z"))
            skip = [p for p in pubs if p[1] > T("2026-12-01T00:00Z")][0]
            nxt = [p for p in pubs if p[1] > skip[1]][0]
            states, _ = self._simulate(f, skip[2] - timedelta(hours=12), nxt[2] + timedelta(hours=12), skip=skip[0])
            for t, r in states:
                if t < skip[2]:
                    self.assertNotIn(r["state"], FR.LATE_STATES, (f, t))
                elif t < nxt[2]:
                    self.assertEqual((r["state"], r["missing"]), ("OVERDUE", [skip[0].isoformat()]), (f, t))
            # tras la siguiente publicación exigible el hueco sigue ahí (la siguiente llegó: have > skip) → CURRENT
            # solo si la fuente trae la nueva; con la nueva también ausente, STALE:
            o = OUT[f]
            r = FR.evaluate(o, RULES[o["rule_id"]], PARAMS[o["rule_id"]], CALS, nxt[2] + timedelta(hours=1),
                            {max(d for d, due, _ in pubs if due < skip[1])})
            self.assertEqual(r["state"], "STALE", f)


# ── A1/A2/A3 · historial de evidencia desde Ingest.publish ──────────────────────────────────────────────────────
class Evidence(unittest.TestCase):
    CSV = b"DATE,NOM10,REAL10,BE10\n20260922,2.5,1.0,1.5\n20260923,2.6,1.1,1.5\n"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        shutil.copytree(os.path.join(ROOT, "sources"), os.path.join(self.root, "sources"))
        os.makedirs(os.path.join(self.root, "data"))
        with open(os.path.join(self.root, "data", "X.csv"), "wb") as fh:
            fh.write(self.CSV)

    def ctx(self, lm="Wed, 23 Sep 2026 20:00:00 GMT"):
        c = ING.Ingest("f3test", root=self.root, now=lambda: T("2026-09-24T07:00Z").timestamp())
        c.requests_log.append({"cls": g8http.OK, "url": "https://example.invalid/x", "status": 200,
                               "date_header": "Thu, 24 Sep 2026 07:00:00 GMT", "last_modified": lm})
        return c

    def lines(self):
        with open(os.path.join(self.root, "data", "_ingest", "evidence", "X.csv.jsonl"), encoding="utf-8") as fh:
            return [json.loads(x) for x in fh]

    def test_same_date_revision_of_other_measure_is_new_evidence(self):
        c = self.ctx()
        data = self.CSV.replace(b"20260923,2.6,1.1,1.5", b"20260923,2.6,1.2,1.4")
        c.publish("X.csv", data, revision_window=1, measures=("NOM10", "REAL10", "BE10"))
        (line,) = self.lines()
        (o,) = line["obs"]
        self.assertEqual((o["date"], o["kind"]), ("2026-09-23", "revision"))
        self.assertNotEqual(o["version"], o["previous_version"])

    def test_rereading_same_download_does_not_duplicate(self):
        c = self.ctx()
        c.publish("X.csv", self.CSV + b"20260924,2.7,1.1,1.6\n")
        c.publish("X.csv", self.CSV + b"20260924,2.7,1.1,1.6\n")      # mismo run y mismos bytes = mismo download_id
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.lines()[0]["obs"][0]["kind"], "new")

    def test_last_modified_is_metadata_not_publication(self):
        self.ctx().publish("X.csv", self.CSV + b"20260924,2.7,1.1,1.6\n")
        (line,) = self.lines()
        self.assertEqual(line["responses"][0]["last_modified"], "Wed, 23 Sep 2026 20:00:00 GMT")
        self.assertIn("no son la hora de publicación", line["responses"][0]["meaning"])
        self.assertNotIn("declared_pub_utc", json.dumps(line))

    def test_failed_download_leaves_no_evidence(self):
        c = ING.Ingest("f3test", root=self.root, now=lambda: T("2026-09-24T07:00Z").timestamp())
        c.requests_log.append({"cls": "FAIL_TRANSIENT", "url": "https://example.invalid/x", "status": 503})
        c.publish("X.csv", self.CSV)
        self.assertFalse(os.path.exists(os.path.join(self.root, "data", "_ingest", "evidence", "X.csv.jsonl")))

    # C3-4 · evidencia por fecha y versión, sin recorte, e intervalos por versión
    def publish_at(self, at, body, **kw):
        c = ING.Ingest("f3test", root=self.root, now=lambda: T(at).timestamp())
        c.requests_log.append({"cls": g8http.OK, "url": "https://example.invalid/x", "status": 200})
        return c.publish("X.csv", body, **kw)

    def test_first_download_records_every_date(self):
        os.remove(os.path.join(self.root, "data", "X.csv"))
        self.publish_at("2026-09-24T09:00Z", self.CSV)
        (line,) = self.lines()
        self.assertEqual([(o["date"], o["kind"]) for o in line["obs"]], [("2026-09-22", "new"), ("2026-09-23", "new")])
        self.assertTrue(line["obs_complete"])

    def test_noop_records_query_without_changes(self):
        self.publish_at("2026-09-24T09:00Z", self.CSV)
        (line,) = self.lines()
        self.assertEqual((line["obs"], line["status"], line["src_max"]), ([], "NOOP", "2026-09-23"))

    def test_more_than_fifty_revisions_all_kept(self):
        dates = [(date(2026, 7, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(60)]
        with open(os.path.join(self.root, "data", "Y.csv"), "w") as fh:
            fh.write("DATE,CLOSE\n" + "".join(d + ",1\n" for d in dates))
        c = ING.Ingest("f3test", root=self.root, now=lambda: T("2026-09-24T10:00Z").timestamp())
        c.requests_log.append({"cls": g8http.OK, "url": "https://example.invalid/y", "status": 200})
        c.publish("Y.csv", ("DATE,CLOSE\n" + "".join(d + ",2\n" for d in dates)).encode(), revision_window=100)
        with open(os.path.join(self.root, "data", "_ingest", "evidence", "Y.csv.jsonl")) as fh:
            (line,) = [json.loads(x) for x in fh]
        self.assertEqual(len(line["obs"]), 60)
        self.assertIn("2026-07-01", {o["date"] for o in line["obs"]})
        self.assertEqual((line["obs_truncated"], line["obs_complete"]), (False, True))

    def test_above_limit_ranges_cover_every_date_and_are_declared(self):
        with mock.patch.object(ING, "EVIDENCE_MAX_OBS", 5):
            dates = [(date(2026, 7, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(8)]
            with open(os.path.join(self.root, "data", "Y.csv"), "w") as fh:
                fh.write("DATE,CLOSE\n" + "".join(d + ",1\n" for d in dates))
            c = ING.Ingest("f3test", root=self.root, now=lambda: T("2026-09-24T10:00Z").timestamp())
            c.requests_log.append({"cls": g8http.OK, "url": "https://example.invalid/y", "status": 200})
            c.publish("Y.csv", ("DATE,CLOSE\n" + "".join(d + ",2\n" for d in dates)).encode(), revision_window=100)
        with open(os.path.join(self.root, "data", "_ingest", "evidence", "Y.csv.jsonl")) as fh:
            (line,) = [json.loads(x) for x in fh]
        self.assertEqual((line["obs_complete"], line["obs_count"]), (False, 8))
        self.assertEqual(sum(r["n"] for r in line["obs_ranges"]), 8)
        a = FR.availability([line], "2026-07-03")
        self.assertIn("sin versión por fecha", a["limitation"])

    def test_same_date_revision_has_its_own_interval(self):
        new = self.CSV.replace(b"20260923,2.6,1.1,1.5", b"20260923,2.6,1.2,1.4")
        m = ("NOM10", "REAL10", "BE10")
        self.publish_at("2026-09-24T09:00Z", self.CSV, revision_window=1, measures=m)
        self.publish_at("2026-09-24T10:00Z", new, revision_window=1, measures=m)
        recs = self.lines()
        b = recs[1]["obs"][0]["version"]
        a = FR.availability(recs, "2026-09-23", version=b)
        self.assertEqual((a["lower_bound"], a["upper_bound"]), ("2026-09-24T09:00:00Z", "2026-09-24T10:00:00Z"))
        first = FR.availability(recs, "2026-09-23", version=recs[1]["obs"][0]["previous_version"])
        self.assertEqual((first["lower_bound"], first["upper_bound"]), (None, "2026-09-24T09:00:00Z"))   # extremo ausente
        self.assertEqual(FR.availability(recs, "2026-09-23")["version"], b)      # por defecto: la versión más reciente

    def test_return_to_previous_version_is_a_new_episode(self):
        new = self.CSV.replace(b"20260923,2.6,1.1,1.5", b"20260923,2.6,1.2,1.4")
        m = ("NOM10", "REAL10", "BE10")
        self.publish_at("2026-09-24T09:00Z", new, revision_window=1, measures=m)     # A → B (publicada)
        self.publish_at("2026-09-24T10:00Z", self.CSV, revision_window=1, measures=m)  # B → A de nuevo
        recs = self.lines()
        va, vb = recs[1]["obs"][0]["version"], recs[0]["obs"][0]["version"]
        self.assertEqual(recs[1]["obs"][0]["previous_version"], vb)
        self.assertEqual(recs[0]["obs"][0]["previous_version"], va)       # A ya existía antes (no observada en consulta)
        eps = FR.availability(recs, "2026-09-23", version=va)
        self.assertEqual([e["version"] for e in eps["episodes"]], [vb, va])  # solo episodios OBSERVADOS
        self.assertEqual((eps["lower_bound"], eps["upper_bound"]), ("2026-09-24T09:00:00Z", "2026-09-24T10:00:00Z"))

    def test_availability_interval_ignores_failed_queries(self):
        recs = [{"ok": True, "query_utc": "2026-09-22T21:30:00Z", "src_max": "2026-09-21"},
                {"ok": False, "query_utc": "2026-09-23T06:00:00Z"},
                {"ok": True, "query_utc": "2026-09-23T13:17:00Z", "src_max": "2026-09-22"}]
        a = FR.availability(recs, "2026-09-22")
        self.assertEqual((a["lower_bound"], a["upper_bound"]), ("2026-09-22T21:30:00Z", "2026-09-23T13:17:00Z"))
        a = FR.availability(recs[2:], "2026-09-22")
        self.assertEqual((a["lower_bound"], a["upper_bound"]), (None, "2026-09-23T13:17:00Z"))   # extremo ausente
        self.assertIn("endpoint consultado", a["scope"])


# ── A1.3 · correspondencia completa ─────────────────────────────────────────────────────────────────────────────
class Mapping(unittest.TestCase):
    def test_outputs_match_rules_one_to_one_and_params_exist(self):
        files = [f for r in RULES.values() for f in filter(None, r["files"].split(";"))]
        self.assertEqual(sorted(files), sorted(OUT))
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(set(RULES), set(PARAMS))
        for o in OUTPUTS:
            self.assertEqual(o["rule_id"], [k for k, r in RULES.items() if o["file"] in r["files"].split(";")][0])

    def test_every_data_file_has_an_output_or_a_documented_exclusion(self):
        files = {os.path.relpath(p, os.path.join(ROOT, "data"))
                 for p in glob.glob(os.path.join(ROOT, "data", "*.csv")) + glob.glob(os.path.join(ROOT, "data", "*.json"))}
        extra = {"BOOK_RISK.json"}                              # derivado del libro (test_f1_config), sin frescura propia
        self.assertEqual(sorted(files - set(OUT) - extra), [])

    def test_registry_feeds_map_to_outputs(self):
        with open(os.path.join(ROOT, "sources", "registry.csv"), encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["status"].startswith("EXISTS") and r["primary_access"].startswith("data/"):
                    self.assertIn(os.path.relpath(r["primary_access"], "data"), OUT, r["feed_id"])

    def test_derived_inputs_and_drivers_are_known_outputs(self):
        for o in OUTPUTS:
            ins = [x for x in o["inputs"].split(";") if x]
            for f in ins:
                self.assertIn(f, OUT, (o["file"], f))
            for f in filter(None, o["drivers"].split(";")):
                self.assertIn(f, ins, (o["file"], f))
            if PARAMS[o["rule_id"]]["model"] == "derived" and o["file"].endswith(".csv") and o["file"] not in (
                    "MFV_G8_walkforward_XAU.csv", "MFV_G8_walkforward_XAG.csv"):
                self.assertTrue(ins, o["file"])

    def test_g_is_per_rule_with_basis_and_status(self):
        for k, p in PARAMS.items():
            self.assertTrue(p["g_basis"].strip(), k)
            self.assertIn(p["g_status"], ("PROVISIONAL", "APROBADO"), k)
            timed = p["model"] in ("bd_offset", "prev_month_end") or p["model"].startswith("weekly_before:")
            if (RULES[k].get("pub_local") or "").strip() and timed:
                self.assertTrue(p["g_min"].strip(), k)                  # hora conocida → margen explícito
            if not (RULES[k].get("pub_local") or "").strip() and p["model"] == "bd_offset":
                self.assertEqual(p["g_min"].strip(), "", k)             # hora desconocida → sin margen inventado


# ── A5 · T21 / T22 sobre el historial real (git) y comparación con el motor actual ─────────────────────────────
@unittest.skipUnless(has_git_ref(), "historial de git con %s no disponible" % BASE_REF)
class History(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import replay_freshness as RP
        cls.t21 = RP.replay(ROOT, BASE_REF, T("2026-09-18T00:00Z"), T("2026-09-24T12:00Z"), 30,
                            {"NZD_BOND_10Y.csv", "TONA.csv", "CHF_NOM_10Y.csv"})
        cls.t22 = RP.replay(ROOT, BASE_REF, T("2026-09-09T00:00Z"), T("2026-09-24T23:00Z"), 60)

    @staticmethod
    def state_at(trans, t):
        cur = None
        for x in trans:
            if T(x["at"]) <= t:
                cur = x
        return cur

    def test_t21_nzd_transitions_never_current_during_outage(self):
        tr = self.t21["transitions"]["NZD_BOND_10Y.csv"]
        self.assertEqual(self.state_at(tr, T("2026-09-21T05:30Z"))["state"], "OVERDUE")
        self.assertEqual(self.state_at(tr, T("2026-09-21T05:30Z"))["missing"], ["2026-09-18"])
        self.assertEqual(self.state_at(tr, T("2026-09-22T05:30Z"))["state"], "STALE")
        t = T("2026-09-21T05:30Z")
        while t < T("2026-09-24T04:00Z"):
            self.assertIn(self.state_at(tr, t)["state"], FR.LATE_STATES, t)
            t += timedelta(minutes=30)
        self.assertEqual(self.state_at(tr, T("2026-09-24T05:00Z"))["state"], "CURRENT")
        # el motor actual no lo vio (presupuesto en días hábiles): el nuevo es más estricto, nunca más permisivo
        self.assertEqual([x["state"] for x in self.t21["current_engine_transitions"]["NZD_NOM_RBNZ"]], ["LIVE"])

    def test_t21_tona_no_false_delay_on_japanese_holidays(self):
        tr = self.t21["transitions"]["TONA.csv"]
        t = T("2026-09-18T21:00Z")
        while t < T("2026-09-23T23:50Z"):
            self.assertNotIn(self.state_at(tr, t)["state"], FR.LATE_STATES + ("DUE",), t)
            t += timedelta(minutes=30)

    def test_t22_properties_over_every_instant(self):
        for f, tr in self.t22["transitions"].items():
            for x in tr:
                if x["state"] in ("CURRENT", "NO_PUBLICATION"):
                    self.assertEqual(x["missing"], [], (f, x))           # nunca «al día» con un hueco
                if x["state"] in ("OVERDUE", "STALE"):
                    self.assertTrue(x["missing"], (f, x))
                    self.assertIsNotNone(x["cause"], (f, x))
                    # sin registros de ejecución en el historial: ninguna causa puede presentarse como registrada
                    self.assertIn(x["cause_certainty"], ("incierta", "configuracion"), (f, x))
                    self.assertNotIn(x["cause"], ("CONSULTADA_SIN_NOVEDAD", "EJECUTADA_CON_FALLO", "RECIBIDA_RETENIDA"))

    def test_t22_known_real_delays_and_unknown_schedules(self):
        tr = self.t22["transitions"]["FLOOR_CAD.csv"]
        for t in (T("2026-09-11T12:00Z"), T("2026-09-12T12:00Z"), T("2026-09-13T12:00Z")):
            self.assertIn(self.state_at(tr, t)["state"], FR.LATE_STATES, t)
        for f in ("GB_POLICY.csv", "JP_POLICY.csv", "CH_POLICY.csv", "AU_POLICY.csv", "NZD_OCR.csv"):
            self.assertEqual({x["state"] for x in self.t22["transitions"][f]}, {"UNKNOWN_SCHEDULE"}, f)

    def test_t22_leniency_versus_current_engine_is_only_the_documented_set(self):
        feeds = {a["feed_id"] for a in self.t22["ampliaciones"]}
        self.assertEqual(feeds, {"CHF_ACM", "NZD_CASH_ON"})          # ambos documentados en la entrega (a decidir)


# ── informe: solo escribe sus salidas y no toca el motor oficial ────────────────────────────────────────────────
class Report(unittest.TestCase):
    def test_report_writes_only_its_outputs(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        before = {p: os.path.getmtime(p) for p in glob.glob(os.path.join(ROOT, "data", "*.csv"))}
        rc = FRR.main(["--at", "2026-09-24T12:00:00Z", "--out", os.path.join(tmp, "f.json"),
                       "--compare-dir", os.path.join(tmp, "cmp")])
        self.assertEqual(rc, 0)
        with open(os.path.join(tmp, "f.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        self.assertEqual((rep["mode"], len(rep["outputs"])), ("PARALELO_SIN_AVISOS", len(OUTPUTS)))
        self.assertEqual(len(os.listdir(os.path.join(tmp, "cmp"))), 1)
        self.assertEqual(before, {p: os.path.getmtime(p) for p in glob.glob(os.path.join(ROOT, "data", "*.csv"))})

    def test_official_engine_untouched_and_workflow_step_non_blocking(self):
        with open(os.path.join(ROOT, "scripts", "dashboard_alerts.py"), "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(),
                             "8569a4753c00a560a100ab6d98477b9ea911dae678edf4dae88e37264cd67d10")
        with open(os.path.join(ROOT, ".github", "workflows", "ingest_watch.yml"), encoding="utf-8") as fh:
            wf = fh.read()
        step = wf[wf.index("scripts/freshness_report.py") - 400: wf.index("scripts/freshness_report.py") + 60]
        self.assertIn("continue-on-error: true", step)
        self.assertNotIn("TELEGRAM", step)                               # sin avisos
        for f in (".github/workflows/daily_update.yml", ".github/workflows/cme_options.yml"):
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                self.assertNotIn("freshness_report", fh.read(), f)       # FINAL y opciones sin cambios


if __name__ == "__main__":
    unittest.main()

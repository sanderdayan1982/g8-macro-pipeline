"""FAMILIAS-00 parte A — votes.csv. Fixtures sintéticos, sin red y sin correos reales.

El parser es puro (cuerpos → filas). El cliente Gmail se sustituye por una función HTTP.
"""
import contextlib
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date, datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

SESSION = date(2026, 11, 12)
BAR = V.expected_bar_close_ms(SESSION)
CCYS = V.CCYS
CANARY = "CANARY_TOKEN_VALUE"
CANARY_SECRET = "CANARY_SECRET_VALUE"


def env_secrets():
    return {
        "GMAIL_OAUTH_CLIENT_ID": "client-id.apps.googleusercontent.com",
        "GMAIL_OAUTH_CLIENT_SECRET": CANARY_SECRET,
        "GMAIL_READONLY_REFRESH_TOKEN": "refresh-token",
        "TELEGRAM_BOT_TOKEN": CANARY,
        "TELEGRAM_CHAT_ID": "123",
    }


def mail(name, fields, part=1, parts=None, sent=1000, gmail_id=None, bar=None, snap=None,
         transport_rev="1.2", batch="DATA", extra=None):
    spec = V.MODULE[name]
    parts = spec["parts"] if parts is None else parts
    bar = BAR if bar is None else bar
    snap = snap or "%s:inst:FX:EURUSD:%s:%s" % (name, bar, sent)
    gmail_id = gmail_id or "%s-p%s-%s" % (name, part, sent)
    bits = ["G8", "SCHEMA=1", "MODULE=%s" % name, "SCRIPT_VERSION=%s" % spec["version"],
            "INSTANCE=i", "HOST=FX:EURUSD", "TF=1D",
            "BAR_CLOSE_MS=%s" % bar, "SENT_MS=%s" % sent, "SNAPSHOT_ID=%s" % snap,
            "BATCH_STATUS=%s" % batch]
    if not spec["monolithic"]:
        bits[4:4] = [
            "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=%s" % transport_rev,
            "PART=%s" % part, "PARTS=%s" % parts,
            "MESSAGE_ID=%s:P%s" % (snap, part),
        ]
    for key, val in fields.items():
        bits.append("%s=%s" % (key, val))
    if extra:
        bits.extend(extra)
    return {"id": gmail_id, "date": "2026-11-13T01:10:00Z",
            "subject": "Alert: %s" % name, "body": "|".join(bits)}


def parts(name, fields, sent=1000, bar=None):
    n = V.MODULE[name]["parts"]
    keys = list(fields.items())
    out = []
    for i in range(1, n + 1):
        chunk = dict(keys[i - 1::n]) if keys else {}
        out.append(mail(name, chunk, part=i, parts=n, sent=sent,
                        gmail_id="%s-p%s" % (name, i), bar=bar))
    return out


def ccy_fields(pattern):
    out = {}
    for ccy in CCYS:
        for key, val in pattern(ccy).items():
            out[key] = val
    return out


def happy_messages():
    msgs = [mail("RISK_G8", {"REGIME": "RISK-OFF", "SCORE": "1.275978"}, gmail_id="risk-1")]
    msgs += parts("XCCY_G8", ccy_fields(lambda c: {"%s_REGIME" % c: "GAP", "%s_STATUS" % c: "FRESH"}))
    msgs.append(mail("XCCY_OIS_SHADOW", ccy_fields(lambda c: {"%s_REGIME" % c: "SIN UMBRAL"}),
                     gmail_id="ois-1"))
    msgs += parts("PSI_G8", ccy_fields(lambda c: {
        "%s_HEALTH" % c: "NEUTRAL", "%s_STATUS" % c: "FRESH", "%s_P_HEALTHY_BP" % c: "12"}))
    msgs += parts("CURVA_G8", ccy_fields(lambda c: {
        "%s_REGIME" % c: "NORMAL", "%s_STATUS" % c: "FRESH", "%s_SPREAD_BP" % c: "3.5"}))
    msgs += parts("RTF10_G8", ccy_fields(lambda c: {"%s_DRIVER" % c: "TP_SAFE", "%s_STATUS" % c: "FRESH"}))
    msgs += parts("POL_G8", ccy_fields(lambda c: {
        "%s_STATE" % c: "REPRICE", "%s_NOW_DIRECTION" % c: "HAWKISH",
        "%s_REPRICE_COUNTED" % c: "PATH", "%s_HAWK_SCORE" % c: "0.4",
        "%s_STATUS" % c: "FRESH", "%s_CLASS_ELIGIBLE" % c: "YES"}))
    msgs += parts("IYDT_2Y", ccy_fields(lambda c: {
        "Y2_%s_SCORE" % c: "0.7519", "Y2_%s_SCORE_BAND" % c: "POS_BELOW_STRONG",
        "Y2_%s_STATUS" % c: "FRESH"}))
    msgs += parts("IYDT_10Y", ccy_fields(lambda c: {
        "Y10_%s_SCORE" % c: "0.10", "Y10_%s_SCORE_BAND" % c: "NEG_BELOW_STRONG",
        "Y10_%s_STATUS" % c: "FRESH"}))
    msgs += parts("FFVA_G8", ccy_fields(lambda c: {
        "%s_REGIME" % c: "DIST", "%s_STATUS" % c: "FRESH", "%s_ROLL" % c: "NO",
        "%s_DIRECTIONAL_INTENSITY" % c: "0.8"}))
    msgs += parts("TOT_G8", ccy_fields(lambda c: {
        "TOT_%s_SCORE" % c: "0.2", "TOT_%s_REGIME" % c: "NEUTRAL", "TOT_%s_STATUS" % c: "FRESH"}))
    msgs += parts("EQF_G8", ccy_fields(lambda c: {
        "EQF_%s_SCORE" % c: "0.3", "EQF_%s_REGIME" % c: "NEUTRAL", "EQF_%s_STATUS" % c: "FRESH"}))
    msgs += parts("POS_G8", ccy_fields(lambda c: {
        "%s_LF_STATE" % c: "CROWD SHORT", "%s_LF_Z" % c: "-2.665", "%s_LF_STATUS" % c: "FRESH"}))
    return msgs


def factor(as_of="2026-11-12", generated="2026-11-12T18:00:00Z", values=None):
    values = values if values is not None else {
        "EUR": "-0.38", "GBP": "0.962", "JPY": "0", "CHF": "0.009",
        "CAD": "-1", "AUD": "0.5", "NZD": "0.0",
    }
    return {
        "version": "1.0.1", "as_of": as_of, "generated_utc": generated,
        "currencies": [{"ccy": ccy, "E21": json.loads(num)} for ccy, num in values.items()],
    }


def index(rows):
    return {(r["script"], r["ccy"]): r for r in rows}


class CloseClock(unittest.TestCase):
    def test_winter_close_is_2300_wat_and_summer_is_2200(self):
        winter = datetime.fromtimestamp(V.expected_bar_close_ms(date(2026, 11, 12)) / 1000, tz=V.WAT)
        summer = datetime.fromtimestamp(V.expected_bar_close_ms(date(2026, 7, 15)) / 1000, tz=V.WAT)
        self.assertEqual((winter.hour, winter.minute, winter.date()), (23, 0, date(2026, 11, 12)))
        self.assertEqual((summer.hour, summer.minute), (22, 0))

    def test_attempt_flips_at_0500_wat(self):
        before = datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc)   # 04:00 WAT
        after = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)    # 05:00 WAT
        self.assertEqual(V.attempt_for(SESSION, before), "first")
        self.assertEqual(V.attempt_for(SESSION, after), "final")


class Parse(unittest.TestCase):
    def test_host_may_contain_colon_and_json(self):
        body = 'G8|MODULE=FFVA_G8|HOST={"sym":"FX:EURUSD","n":1}|BAR_CLOSE_MS=1'
        got = V.parse_payload(body)
        self.assertEqual(got["HOST"], '{"sym":"FX:EURUSD","n":1}')

    def test_non_g8_body_is_ignored(self):
        self.assertIsNone(V.parse_payload("hello"))
        rows = V.rows_from_messages(
            [{"id": "x", "date": "2026-11-13T01:00:00Z", "subject": "Alert: other", "body": "nope"}],
            SESSION, True)
        self.assertTrue(all(r["estado"] == "MISSING" for r in rows))


class HappyPath(unittest.TestCase):
    def test_thirteen_modules(self):
        rows = index(V.rows_from_messages(happy_messages(), SESSION, True))
        self.assertEqual(len(rows), 1 + 12 * 8)
        risk = rows[("RISK_G8", "G8")]
        self.assertEqual((risk["estado"], risk["valor"], risk["voto"], risk["stale"], risk["version"]),
                         ("RISK-OFF", "1.275978", "NA", "false", "1.2.1"))
        eur = rows[("XCCY_G8", "EUR")]
        self.assertEqual((eur["estado"], eur["voto"], eur["parts_expected"], eur["parts_received"]),
                         ("GAP", "NA", "2", "2"))
        self.assertEqual(rows[("XCCY_OIS_SHADOW", "EUR")]["estado"], "SIN UMBRAL")
        self.assertEqual(rows[("PSI_G8", "EUR")]["estado"], "NEUTRAL")
        self.assertEqual(rows[("PSI_G8", "EUR")]["valor"], "12")
        self.assertEqual(rows[("CURVA_G8", "EUR")]["valor"], "3.5")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "TP_SAFE")
        self.assertEqual(rows[("POL_G8", "EUR")]["voto"], "+1")
        self.assertEqual((rows[("IYDT_2Y", "EUR")]["voto"], rows[("IYDT_2Y", "EUR")]["valor"]), ("0", "0.7519"))
        self.assertEqual(rows[("IYDT_10Y", "EUR")]["voto"], "0")
        self.assertEqual(rows[("FFVA_G8", "EUR")]["voto"], "-1")
        self.assertEqual(rows[("FFVA_G8", "EUR")]["estado"], "DIST")
        self.assertEqual(rows[("TOT_G8", "EUR")]["estado"], "NEUTRAL")
        self.assertEqual(rows[("EQF_G8", "EUR")]["valor"], "0.3")
        pos = rows[("POS_G8", "EUR")]
        self.assertEqual((pos["estado"], pos["valor"], pos["voto"]), ("CROWD SHORT", "-2.665", "NA"))
        self.assertTrue(all(r["rule_version"] == "1" for r in rows.values()))
        self.assertTrue(all(r["family"] == V.FAMILIES[r["script"]] for r in rows.values()))
        self.assertNotIn(("RISK_G8", "EUR"), rows)


class VoteTable(unittest.TestCase):
    def one(self, name, fields, **kw):
        msgs = parts(name, fields, **kw) if not V.MODULE[name]["monolithic"] else [mail(name, fields, **kw)]
        rows = index(V.rows_from_messages(msgs, SESSION, True))
        return rows[(name, "EUR" if name != "RISK_G8" else "G8")]

    def test_iydt_bands_and_unknown_extreme(self):
        cases = {
            "POS_STRONG": "+1", "NEG_STRONG": "-1",
            "POS_BELOW_STRONG": "0", "NEG_BELOW_STRONG": "0",
            "POS_EXTREME": "NA", "NEG_EXTREME": "NA", "WARM": "NA",
        }
        for band, voto in cases.items():
            row = self.one("IYDT_2Y", {"Y2_EUR_SCORE": "1", "Y2_EUR_SCORE_BAND": band, "Y2_EUR_STATUS": "FRESH"})
            self.assertEqual(row["voto"], voto, band)
            self.assertEqual(row["stale"], "false", band)
        stale = self.one("IYDT_10Y", {"Y10_EUR_SCORE": "1", "Y10_EUR_SCORE_BAND": "POS_STRONG",
                                      "Y10_EUR_STATUS": "STALE"})
        self.assertEqual((stale["voto"], stale["stale"], stale["estado"]), ("NA", "true", "POS_STRONG"))

    def test_ffva_map_roll_and_z_flow(self):
        for regime, voto in (("ACCUM", "+1"), ("SC", "+1"), ("DIST", "-1"), ("LIQ", "-1"), ("FLAT_OI", "0")):
            row = self.one("FFVA_G8", {"EUR_REGIME": regime, "EUR_STATUS": "FRESH", "EUR_ROLL": "NO"})
            self.assertEqual((row["voto"], row["estado"]), (voto, regime))
        rolled = self.one("FFVA_G8", {"EUR_REGIME": "ACCUM", "EUR_STATUS": "FRESH", "EUR_ROLL": "YES"})
        self.assertEqual((rolled["voto"], rolled["stale"], rolled["estado"]), ("NA", "false", "ACCUM"))
        stale = self.one("FFVA_G8", {"EUR_REGIME": "DIST", "EUR_STATUS": "STALE", "EUR_ROLL": "NO"})
        self.assertEqual((stale["voto"], stale["stale"]), ("NA", "true"))
        zrow = self.one("FFVA_G8", {"EUR_REGIME": "FLAT_OI", "EUR_STATUS": "FRESH", "EUR_ROLL": "NO",
                                    "EUR_Z_FLOW": "-1.25"})
        self.assertEqual(zrow["valor"], "-1.25")
        both = self.one("FFVA_G8", {"EUR_REGIME": "FLAT_OI", "EUR_STATUS": "FRESH", "EUR_ROLL": "NO",
                                    "EUR_DIRECTIONAL_INTENSITY": "0.4", "EUR_Z_FLOW": "-1.25"})
        self.assertEqual(both["valor"], "0.4")

    def test_pol_direction_and_eligibility(self):
        base = {"EUR_STATE": "REPRICE", "EUR_STATUS": "FRESH", "EUR_HAWK_SCORE": "0.2"}
        cases = [
            ("FULL", "HAWKISH", "YES", "+1"),
            ("PATH", "DOVISH", "YES", "-1"),
            ("NONE", "HAWKISH", "YES", "0"),
            ("FULL", "HAWKISH", "NO", "NA"),
            ("PATH", "DOVISH", "NO", "NA"),
        ]
        for counted, direction, eligible, voto in cases:
            fields = dict(base, EUR_REPRICE_COUNTED=counted, EUR_NOW_DIRECTION=direction,
                          EUR_CLASS_ELIGIBLE=eligible)
            row = self.one("POL_G8", fields)
            self.assertEqual(row["voto"], voto, (counted, direction, eligible))
        self.assertEqual(self.one("POL_G8", dict(base, EUR_REPRICE_COUNTED="FULL",
                                                 EUR_NOW_DIRECTION="HAWKISH"))["voto"], "NA")

    def test_always_na_keeps_estado_and_number(self):
        self.assertEqual(self.one("PSI_G8", {"EUR_HEALTH": "NEUTRAL", "EUR_STATUS": "FRESH",
                                            "EUR_P_HEALTHY_BP": "4"})["voto"], "NA")
        for name, fields, estado, valor in (
            ("XCCY_G8", {"EUR_REGIME": "GAP", "EUR_STATUS": "FRESH"}, "GAP", ""),
            ("XCCY_OIS_SHADOW", {"EUR_REGIME": "SIN UMBRAL"}, "SIN UMBRAL", ""),
            ("CURVA_G8", {"EUR_REGIME": "NORMAL", "EUR_STATUS": "FRESH", "EUR_SPREAD_BP": "8"}, "NORMAL", "8"),
            ("RTF10_G8", {"EUR_DRIVER": "TP_SAFE", "EUR_STATUS": "FRESH"}, "TP_SAFE", ""),
            ("TOT_G8", {"TOT_EUR_REGIME": "NEUTRAL", "TOT_EUR_STATUS": "FRESH", "TOT_EUR_SCORE": "1.2"}, "NEUTRAL", "1.2"),
            ("EQF_G8", {"EQF_EUR_REGIME": "BID", "EQF_EUR_STATUS": "FRESH", "EQF_EUR_SCORE": "0.7"}, "BID", "0.7"),
            ("POS_G8", {"EUR_LF_STATE": "CROWD SHORT", "EUR_LF_Z": "-2.665", "EUR_LF_STATUS": "FRESH"},
             "CROWD SHORT", "-2.665"),
        ):
            row = self.one(name, fields)
            self.assertEqual((row["voto"], row["estado"], row["valor"], row["stale"]),
                             ("NA", estado, valor, "false"), name)

    def test_bar_close_mismatch_is_stale_and_cancels_the_vote(self):
        late = BAR + 60_000
        row = self.one("IYDT_2Y", {"Y2_EUR_SCORE": "1", "Y2_EUR_SCORE_BAND": "POS_STRONG",
                                  "Y2_EUR_STATUS": "FRESH"}, bar=late)
        self.assertEqual((row["voto"], row["stale"], row["estado"]), ("NA", "true", "POS_STRONG"))


class HardCases(unittest.TestCase):
    def test_missing_only_on_the_final_attempt(self):
        first = V.rows_from_messages([], SESSION, write_missing=False)
        final = V.rows_from_messages([], SESSION, write_missing=True)
        self.assertEqual(first, [])
        self.assertTrue(final)
        self.assertTrue(all(r["estado"] == "MISSING" and r["voto"] == "NA" and r["valor"] == "" for r in final))
        self.assertEqual(len(final), 1 + 12 * 8)

    def test_missing_part_and_error_batch_are_incomplete(self):
        only = [m for m in parts("XCCY_G8", {"EUR_REGIME": "GAP", "EUR_STATUS": "FRESH"}) if "P1" in m["body"] or m["body"].count("PART=1|")]
        only = [m for m in parts("XCCY_G8", {"EUR_REGIME": "GAP", "EUR_STATUS": "FRESH"}) if "|PART=1|" in m["body"]]
        self.assertEqual([r for r in V.rows_from_messages(only, SESSION, False) if r["script"] == "XCCY_G8"], [])
        rows = [r for r in V.rows_from_messages(only, SESSION, True) if r["script"] == "XCCY_G8"]
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(r["estado"] == "INGEST_INCOMPLETE" and r["voto"] == "NA" for r in rows))
        bad = parts("PSI_G8", {"EUR_HEALTH": "NEUTRAL", "EUR_STATUS": "FRESH"})
        bad[0] = mail("PSI_G8", {"EUR_HEALTH": "NEUTRAL"}, part=1, parts=3, batch="ERROR", gmail_id="psi-p1")
        rows = [r for r in V.rows_from_messages(bad, SESSION, True) if r["script"] == "PSI_G8"]
        self.assertTrue(rows)
        self.assertTrue(all(r["estado"] == "INGEST_INCOMPLETE" for r in rows))

    def test_duplicate_message_and_two_snapshots(self):
        one = mail("XCCY_OIS_SHADOW", {"EUR_REGIME": "SIN UMBRAL"}, sent=10, gmail_id="a")
        dup = V.rows_from_messages([one, dict(one)], SESSION, True)
        eur = [r for r in dup if r["script"] == "XCCY_OIS_SHADOW" and r["ccy"] == "EUR"]
        self.assertEqual(len(eur), 1)
        older = parts("XCCY_G8", ccy_fields(lambda c: {"%s_REGIME" % c: "GAP", "%s_STATUS" % c: "FRESH"}), sent=10)
        newer = parts("XCCY_G8", ccy_fields(lambda c: {"%s_REGIME" % c: "TIGHT", "%s_STATUS" % c: "FRESH"}), sent=99)
        rows = index(V.rows_from_messages(older + newer, SESSION, True))
        self.assertEqual(rows[("XCCY_G8", "EUR")]["estado"], "TIGHT")

    def test_status_not_fresh_keeps_estado(self):
        row = index(V.rows_from_messages(
            parts("CURVA_G8", {"EUR_REGIME": "INVERTED", "EUR_STATUS": "STALE", "EUR_SPREAD_BP": "9"}),
            SESSION, True))[("CURVA_G8", "EUR")]
        self.assertEqual((row["estado"], row["valor"], row["voto"], row["stale"]),
                         ("INVERTED", "9", "NA", "true"))


class FactorRows(unittest.TestCase):
    def test_sign_and_stale(self):
        rows = index(V.rows_from_factor(factor(), SESSION))
        self.assertNotIn(("E21", "USD"), rows)
        self.assertEqual(rows[("E21", "EUR")]["voto"], "-1")
        self.assertEqual(rows[("E21", "EUR")]["valor"], "-0.38")
        self.assertEqual(rows[("E21", "JPY")]["voto"], "0")
        self.assertEqual(rows[("E21", "GBP")]["voto"], "+1")
        self.assertEqual(rows[("E21", "EUR")]["family"], "R")
        self.assertEqual(rows[("E21", "EUR")]["stale"], "false")
        older = index(V.rows_from_factor(factor(as_of="2026-11-11"), SESSION))
        self.assertEqual((older[("E21", "EUR")]["voto"], older[("E21", "EUR")]["stale"],
                          older[("E21", "EUR")]["valor"]), ("NA", "true", "-0.38"))
        newer = index(V.rows_from_factor(factor(as_of="2026-11-13"), SESSION))
        self.assertEqual(newer[("E21", "EUR")]["voto"], "NA")
        early = index(V.rows_from_factor(
            factor(generated="2026-11-11T22:00:00Z"), SESSION))
        self.assertEqual(early[("E21", "EUR")]["stale"], "true")


class AppendOnly(unittest.TestCase):
    def test_existing_bytes_stay_and_rerun_appends_nothing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "votes.csv")
        header = ",".join(V.COLUMNS)
        original = (header + "\n2026-11-11,RISK_G8,1.2.1,G8,OLD,,NA,false,,,,,,RISK,1,1,1\n").encode()
        with open(path, "wb") as fh:
            fh.write(original)
        rows = V.rows_from_messages([mail("RISK_G8", {"REGIME": "RISK-OFF", "SCORE": "1"})], SESSION, False)
        self.assertEqual(V.append_rows(path, rows), 1)
        with open(path, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(original), data[:len(original)])
        self.assertEqual(V.append_rows(path, rows), 0)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), data)


class GmailFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.votes = os.path.join(self.tmp, "votes.csv")
        self.weekly = os.path.join(self.tmp, "votes_weekly.json")
        self.factor = os.path.join(self.tmp, "USD_FACTOR.json")
        with open(self.factor, "w", encoding="utf-8") as fh:
            json.dump(factor(values={"EUR": "-0.38", "GBP": "1"}), fh)
        self.sent = []

    def run_attempt(self, attempt, http=None, env=None):
        return V.run(
            ["--attempt", attempt, "--session", "2026-11-12",
             "--votes", self.votes, "--weekly", self.weekly, "--factor", self.factor],
            http=http, sender=self.sent.append, env=env if env is not None else {},
            sleep=lambda _s: None,
            now=datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc),
        )

    def test_failure_on_final_writes_missing_and_one_telegram(self):
        rc = self.run_attempt("final", env={})
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn(CANARY, self.sent[0])
        self.assertNotIn(CANARY_SECRET, self.sent[0])
        rows = list(csv.DictReader(open(self.votes, encoding="utf-8")))
        missing = [r for r in rows if r["estado"] == "MISSING"]
        e21 = [r for r in rows if r["script"] == "E21"]
        self.assertEqual(len(missing), 1 + 12 * 8)
        self.assertTrue(all(r["voto"] == "NA" for r in missing))
        self.assertEqual(sorted(r["ccy"] for r in e21), ["EUR", "GBP"])
        self.assertEqual({r["voto"] for r in e21}, {"-1", "+1"})
        self.assertNotIn("MISSING", {r["estado"] for r in e21})

    def test_retry_does_not_write_missing_until_the_final_attempt(self):
        def boom(method, url, headers, body):
            return 503, {}, b"upstream"

        rc1 = self.run_attempt("first", http=boom, env=env_secrets())
        self.assertEqual(rc1, 1)
        self.assertEqual(len(self.sent), 1)
        rows = list(csv.DictReader(open(self.votes, encoding="utf-8")))
        self.assertTrue(rows)
        self.assertTrue(all(r["script"] == "E21" for r in rows))
        self.assertNotIn("MISSING", {r["estado"] for r in rows})
        before = open(self.votes, "rb").read()
        rc2 = self.run_attempt("final", http=boom, env=env_secrets())
        self.assertEqual(rc2, 1)
        self.assertEqual(len(self.sent), 2)
        after = open(self.votes, "rb").read()
        self.assertTrue(after.startswith(before))
        rows = list(csv.DictReader(open(self.votes, encoding="utf-8")))
        e21 = [r for r in rows if r["script"] == "E21"]
        self.assertEqual(len(e21), 2)
        self.assertEqual(len([r for r in rows if r["estado"] == "MISSING"]), 1 + 12 * 8)
        rc3 = self.run_attempt("final", http=boom, env=env_secrets())
        self.assertEqual(rc3, 1)
        self.assertEqual(open(self.votes, "rb").read(), after)

    def test_empty_inbox_waits_for_the_final_attempt(self):
        def empty(method, url, headers, body):
            if "oauth2.googleapis.com" in url:
                return 200, {}, b'{"access_token":"access"}'
            return 200, {}, b'{"messages":[]}'

        rc = self.run_attempt("first", http=empty, env=env_secrets())
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])
        rows = list(csv.DictReader(open(self.votes, encoding="utf-8")))
        self.assertTrue(all(r["script"] == "E21" for r in rows))
        rc = self.run_attempt("final", http=empty, env=env_secrets())
        self.assertEqual(rc, 0)
        rows = list(csv.DictReader(open(self.votes, encoding="utf-8")))
        self.assertEqual(len([r for r in rows if r["estado"] == "MISSING"]), 1 + 12 * 8)


class GmailClient(unittest.TestCase):
    def test_query_includes_spam_and_does_not_modify(self):
        seen = []

        def http(method, url, headers, body):
            seen.append((method, url, body))
            if "oauth2.googleapis.com/token" in url:
                return 200, {}, b'{"access_token":"access"}'
            if url.startswith(V.LIST_URL + "?"):
                return 200, {}, b'{"messages":[{"id":"m1"}]}'
            if url.startswith(V.LIST_URL + "/m1"):
                return 200, {}, json.dumps({
                    "id": "m1", "internalDate": "1731456600000",
                    "payload": {"mimeType": "text/plain", "headers": [
                        {"name": "Subject", "value": "Alert: RISK_G8"}],
                        "body": {"data": ""}}},
                ).encode()
            raise AssertionError(url)

        msgs = V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(msgs[0]["id"], "m1")
        self.assertTrue(all(method == "GET" or "oauth2.googleapis.com/token" in url
                            for method, url, _body in seen))
        listed = [url for method, url, _body in seen if method == "GET" and "messages?" in url][0]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(listed).query)
        self.assertEqual(qs["q"], [V.GMAIL_QUERY])
        self.assertEqual(qs["includeSpamTrash"], ["true"])
        self.assertNotIn("modify", listed)

    def test_api_error_retries_then_raises_without_echoing_the_body(self):
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            if "oauth2" in url:
                return 200, {}, b'{"access_token":"access"}'
            return 503, {}, b"secret-in-body-" + CANARY.encode()

        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(caught.exception.kind, "api")
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertEqual(sum(1 for url in calls if "oauth2" not in url), V.RETRIES)

    def test_from_json_does_not_touch_http(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        src = os.path.join(tmp, "in.json")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump([mail("RISK_G8", {"REGIME": "RISK-OFF", "SCORE": "1.275978"})], fh)
        factor_path = os.path.join(tmp, "f.json")
        with open(factor_path, "w", encoding="utf-8") as fh:
            json.dump(factor(), fh)

        def http(*_a, **_k):
            raise AssertionError("network")

        rc = V.run(
            ["--from-json", src, "--session", "2026-11-12", "--dry-run", "--attempt", "final",
             "--factor", factor_path, "--votes", os.path.join(tmp, "votes.csv")],
            http=http, sender=lambda _t: (_ for _ in ()).throw(AssertionError("telegram")),
        )
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(tmp, "votes.csv")))

    def test_cli_dry_run_prints_rows(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        src = os.path.join(tmp, "in.json")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump(happy_messages(), fh)
        factor_path = os.path.join(tmp, "f.json")
        with open(factor_path, "w", encoding="utf-8") as fh:
            json.dump(factor(), fh)
        votes = os.path.join(tmp, "votes.csv")
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "votes_ingest.py"),
             "--from-json", src, "--session", "2026-11-12", "--dry-run", "--attempt", "final",
             "--factor", factor_path, "--votes", votes, "--weekly", os.path.join(tmp, "w.json")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("fecha_cierre,script,version,ccy", proc.stdout)
        self.assertIn("RISK-OFF", proc.stdout)
        self.assertIn("1.275978", proc.stdout)
        self.assertFalse(os.path.exists(votes))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "data", "votes.csv")))


class Weekly(unittest.TestCase):
    def test_complete_day_is_counted_and_missing_day_is_not(self):
        rows = V.rows_from_messages(happy_messages(), SESSION, True)
        rows += V.rows_from_factor(factor(), SESSION)
        report = V.weekly_report(rows, "2026-11-13T04:00:00Z")
        self.assertEqual(report["complete_days_total"], 1)
        self.assertEqual(report["weeks"][0]["sessions"], ["2026-11-12"])
        broken = V.rows_from_messages([], SESSION, True) + V.rows_from_factor(factor(), SESSION)
        self.assertEqual(V.weekly_report(broken, "2026-11-13T04:00:00Z")["complete_days_total"], 0)


class TokenOauthError(unittest.TestCase):
    """Token-endpoint non-2xx: whitelisted `error` code, HTTP status, no body."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.votes = os.path.join(self.tmp, "votes.csv")
        self.weekly = os.path.join(self.tmp, "votes_weekly.json")
        self.factor = os.path.join(self.tmp, "USD_FACTOR.json")
        with open(self.factor, "w", encoding="utf-8") as fh:
            json.dump(factor(as_of="2026-11-13", generated="2026-11-13T18:00:00Z",
                             values={"EUR": "-0.38", "GBP": "1"}), fh)
        self.sent = []

    def _run(self, http, env=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = V.run(
                ["--attempt", "final", "--session", "2026-11-13",
                 "--votes", self.votes, "--weekly", self.weekly, "--factor", self.factor],
                http=http, sender=self.sent.append,
                env=env_secrets() if env is None else env,
                sleep=lambda _s: None,
                now=datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc),
            )
        return rc, err.getvalue()

    def _assert_rows_unchanged(self, rc):
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.sent), 1)
        with open(self.votes, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        missing = [r for r in rows if r["estado"] == "MISSING"]
        e21 = [r for r in rows if r["script"] == "E21"]
        self.assertEqual(len(missing), 1 + 12 * 8)
        self.assertTrue(all(r["voto"] == "NA" for r in missing))
        self.assertEqual(sorted(r["ccy"] for r in e21), ["EUR", "GBP"])
        self.assertNotIn("MISSING", {r["estado"] for r in e21})

    def _assert_no_leak(self, text, *needles):
        for needle in needles:
            self.assertNotIn(needle, text)
        self.assertNotIn("error_description", text)
        self.assertNotIn(CANARY, text)
        self.assertNotIn(CANARY_SECRET, text)

    def test_invalid_grant_logs_status_and_code_only(self):
        leak = "LEAK_DESC_invalid_grant_body"
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            return 400, {}, json.dumps({
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked. " + leak,
            }).encode()

        rc, err = self._run(http)
        self._assert_rows_unchanged(rc)
        self.assertEqual(calls, [V.TOKEN_URL])
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 400 oauth_error=invalid_grant")
        self.assertIn(expected, err)
        self.assertIn("HTTP 400 oauth_error=invalid_grant", self.sent[0])
        self._assert_no_leak(err, leak, "expired or revoked")
        self._assert_no_leak(self.sent[0], leak, "expired or revoked")

    def test_invalid_client_logs_status_and_code_only(self):
        leak = "LEAK_DESC_invalid_client_body"
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            return 401, {}, json.dumps({
                "error": "invalid_client",
                "error_description": "The OAuth client was not found. " + leak,
            }).encode()

        rc, err = self._run(http)
        self._assert_rows_unchanged(rc)
        self.assertEqual(len(calls), 1)
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 401 oauth_error=invalid_client")
        self.assertIn(expected, err)
        self.assertIn("HTTP 401 oauth_error=invalid_client", self.sent[0])
        self._assert_no_leak(err, leak, "OAuth client was not found")
        self._assert_no_leak(self.sent[0], leak, "OAuth client was not found")

    def test_unparseable_body_is_unknown_and_does_not_leak(self):
        raw = b"<html>LEAK_RAW_TOKEN_BODY error_description=super-secret-phrase not-json"
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            return 400, {}, raw

        rc, err = self._run(http)
        self._assert_rows_unchanged(rc)
        self.assertEqual(len(calls), 1)
        expected = ("Gmail failure (auth) on attempt final. Session 2026-11-13. "
                    "HTTP 400 oauth_error=unknown")
        self.assertIn(expected, err)
        self.assertIn("HTTP 400 oauth_error=unknown", self.sent[0])
        self._assert_no_leak(err, "LEAK_RAW_TOKEN_BODY", "super-secret-phrase",
                             "not-json", "<html>")
        self._assert_no_leak(self.sent[0], "LEAK_RAW_TOKEN_BODY", "super-secret-phrase",
                             "not-json", "<html>")
        with self.assertRaises(V.GmailError) as caught:
            V.fetch_messages("cid", "sec", "ref", http, sleep=lambda _s: None)
        self.assertEqual(caught.exception.kind, "auth")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.oauth_error, "unknown")
        self._assert_no_leak(str(caught.exception), "LEAK_RAW_TOKEN_BODY",
                             "super-secret-phrase", "<html>")

    def test_non_whitelisted_error_field_is_unknown_and_does_not_leak(self):
        raw_code = "invalid_grant LEAK_BAD_CODE"
        calls = []

        def http(method, url, headers, body):
            calls.append(url)
            return 400, {}, json.dumps({
                "error": raw_code,
                "error_description": "full body should stay off the log LEAK_BAD_DESC",
            }).encode()

        rc, err = self._run(http)
        self._assert_rows_unchanged(rc)
        self.assertEqual(len(calls), 1)
        self.assertIn("oauth_error=unknown", err)
        self.assertNotIn("oauth_error=invalid_grant", err)
        self._assert_no_leak(err, raw_code, "LEAK_BAD_CODE", "LEAK_BAD_DESC",
                             "full body should stay off the log")
        self._assert_no_leak(self.sent[0], raw_code, "LEAK_BAD_CODE", "LEAK_BAD_DESC",
                             "full body should stay off the log")

    def test_list_api_error_does_not_emit_an_oauth_code(self):
        leak = "LEAK_LIST_BODY_not_for_the_log"

        def http(method, url, headers, body):
            if "oauth2.googleapis.com" in url:
                return 200, {}, b'{"access_token":"access"}'
            return 503, {}, json.dumps({
                "error": "invalid_grant",
                "error_description": leak,
            }).encode()

        rc, err = self._run(http)
        self.assertEqual(rc, 1)
        self.assertEqual(
            err.strip(),
            "Gmail failure (api) on attempt final. Session 2026-11-13.",
        )
        self.assertNotIn("oauth_error", err)
        self.assertNotIn("oauth_error", self.sent[0])
        self._assert_no_leak(err, leak, "invalid_grant")
        self._assert_no_leak(self.sent[0], leak, "invalid_grant")


class Weekend(unittest.TestCase):
    def test_no_rows(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        votes = os.path.join(tmp, "votes.csv")
        rc = V.run(["--session", "2026-11-14", "--votes", votes, "--attempt", "final"],
                   http=lambda *a: (_ for _ in ()).throw(AssertionError("network")),
                   sender=lambda _t: (_ for _ in ()).throw(AssertionError("telegram")))
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(votes))


if __name__ == "__main__":
    unittest.main()

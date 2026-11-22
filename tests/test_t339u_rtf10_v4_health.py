"""t339u: estado RTF10 v4 = salud del dato; convivencia con v3.10.

El estado no se deriva de READING / READING_MAIN ni de la dirección.
{CCY}_STATUS del pine es FRESH / STALE / NA; solo FRESH cuenta.
voto sigue NA. Tests nuevos solamente; no se editan tests existentes.
t339u, orden expresa Sander 22-nov.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

from test_t337u_rtf10_v4_parser import (  # noqa: E402
    BAR,
    CCYS,
    SESSION,
    index,
    merged_from,
    pack_v4_sample,
    v3_mails,
)


def _driver_fields():
    out = {}
    for ccy in CCYS:
        out["%s_DRIVER" % ccy] = "TP_SAFE"
        out["%s_STATUS" % ccy] = "FRESH"
    return out


def _v3_later_than_v4():
    """v3 del mismo día con SENT_MS posterior al paquete v4 de ejemplo."""
    mails = v3_mails(_driver_fields(), alert_version="3")
    later = "1764199999999"
    out = []
    for m in mails:
        body = m["body"].replace("SENT_MS=1000", "SENT_MS=%s" % later)
        # El snap de v3_mails usa sent=1000; lo dejamos, es otro SNAPSHOT_ID.
        out.append(dict(m, body=body, id=m["id"] + "-late"))
    return out


def _v4_with_status(status_by_ccy, batch="DATA"):
    mails, snap = pack_v4_sample()
    if status_by_ccy or batch != "DATA":
        patched = []
        for m in mails:
            body = m["body"]
            for ccy, st in status_by_ccy.items():
                body = body.replace("%s_STATUS=FRESH" % ccy, "%s_STATUS=%s" % (ccy, st), 1)
            if batch != "DATA":
                body = body.replace("BATCH_STATUS=DATA", "BATCH_STATUS=%s" % batch, 1)
            patched.append(dict(m, body=body))
        mails = patched
    return mails, snap


def _v4_error_packet():
    """Paquete ERROR del pine: PART=0, BATCH_STATUS=ERROR, un solo correo."""
    sent = 1764100000000
    inst = "RTF10_MAIN"
    snap = "RTF10_G8:%s:FX:EURUSD:%s:%s" % (inst, BAR, sent)
    body = (
        "G8|SCHEMA=1|MODULE=RTF10_G8|SCRIPT_VERSION=4.0.0|ALERT_VERSION=4"
        "|TRANSPORT=G8_PARTS_V1|TRANSPORT_REV=1.3"
        "|INSTANCE=%s|HOST=FX:EURUSD|TF=1D|BAR_CLOSE_MS=%s"
        "|SENT_MS=%s|EVENT=DAILY_SNAPSHOT|SNAPSHOT_ID=%s"
        "|MESSAGE_ID=%s:ERROR|PART=0|PARTS=0|BATCH_STATUS=ERROR"
        "|DATA_COMPLETE=NO|TRANSPORT_ERROR=TOO_MANY_PARTS|ERRORS_N=0"
        "|PARTS_NEEDED=15|MAX_PARTS=14"
        % (inst, BAR, sent, snap, snap)
    )
    return [{
        "id": "rtf10-v4-error",
        "date": "2026-11-21T21:05:00Z",
        "subject": "Alert: RTF10_G8",
        "body": body,
    }], snap


class FreshValueFromPine(unittest.TestCase):
    def test_only_literal_fresh_counts(self):
        # f_status / f_worst del pine: FRESH, STALE, NA. Nada más.
        kv = {"ALERT_VERSION": "4", "BATCH_STATUS": "DATA"}
        for ccy in CCYS:
            kv["%s_STATUS" % ccy] = "FRESH"
        self.assertEqual(V._rtf10_fresh_n(kv), 8)
        kv["EUR_STATUS"] = "STALE"
        kv["GBP_STATUS"] = "NA"
        kv["JPY_STATUS"] = "FRESH"
        self.assertEqual(V._rtf10_fresh_n(kv), 6)
        kv["AUD_STATUS"] = "fresh"  # minúsculas: no es el token del pine
        self.assertEqual(V._rtf10_fresh_n(kv), 5)


class V4HealthEstado(unittest.TestCase):
    def test_ok_when_data_and_at_least_six_fresh(self):
        mails, _ = _v4_with_status({})  # 8 FRESH
        rows = index(V.rows_from_messages(mails, SESSION, True))
        for ccy in CCYS:
            row = rows[("RTF10_G8", ccy)]
            self.assertEqual(row["estado"], "OK")
            self.assertEqual(row["voto"], "NA")
            self.assertEqual(row["valor"], "")
            self.assertEqual(row["version"], "4.0.0")

    def test_ok_at_exactly_six_fresh(self):
        stale = {"NZD": "STALE", "CAD": "NA"}
        mails, _ = _v4_with_status(stale)
        merged = merged_from(mails)
        self.assertEqual(V._rtf10_fresh_n(merged), 6)
        rows = index(V.rows_from_messages(mails, SESSION, True))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "OK")
        self.assertEqual(rows[("RTF10_G8", "NZD")]["estado"], "OK")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["voto"], "NA")

    def test_degraded_when_data_and_fewer_than_six_fresh(self):
        stale = {"EUR": "STALE", "GBP": "STALE", "JPY": "NA"}
        mails, _ = _v4_with_status(stale)
        merged = merged_from(mails)
        self.assertEqual(V._rtf10_fresh_n(merged), 5)
        rows = index(V.rows_from_messages(mails, SESSION, True))
        for ccy in CCYS:
            self.assertEqual(rows[("RTF10_G8", ccy)]["estado"], "DEGRADED")
            self.assertEqual(rows[("RTF10_G8", ccy)]["voto"], "NA")

    def test_error_when_batch_status_error(self):
        mails, snap = _v4_error_packet()
        rows = index(V.rows_from_messages(mails, SESSION, False))
        for ccy in CCYS:
            row = rows[("RTF10_G8", ccy)]
            self.assertEqual(row["estado"], "ERROR")
            self.assertEqual(row["voto"], "NA")
            self.assertEqual(row["snapshot_id"], snap)
        # No es INGEST_INCOMPLETE: el paquete ERROR es definitivo.
        self.assertTrue(all(r["estado"] == "ERROR" for r in rows.values()
                            if r["script"] == "RTF10_G8"))

    def test_estado_is_not_reading_or_driver(self):
        mails, _ = pack_v4_sample(extra="|EUR_DRIVER=TP_FISCAL")
        merged = merged_from(mails)
        rows = index(V.rows_from_messages(mails, SESSION, True))
        eur = rows[("RTF10_G8", "EUR")]
        self.assertEqual(eur["estado"], "OK")
        self.assertNotEqual(eur["estado"], merged["EUR_READING"])
        self.assertNotEqual(eur["estado"], merged["EUR_READING_MAIN"])
        self.assertNotEqual(eur["estado"], "TP_FISCAL")
        self.assertEqual(eur["voto"], "NA")

    def test_stale_means_no_v4_at_final_d1_attempt(self):
        # El intento final (write_missing=True) es el mecanismo ya existente
        # del cierre D1 (05:20 WAT, tras expected_bar_close_ms y el corte 05:15).
        # STALE solo rige desde la fecha del primer correo v4.
        first = SESSION
        self.assertEqual(V._rtf10_stale_if_no_v4(True, SESSION, first), "STALE")
        self.assertIsNone(V._rtf10_stale_if_no_v4(False, SESSION, first))
        self.assertIsNone(V._rtf10_stale_if_no_v4(True, SESSION, None))


class CoexistenceV3DoesNotOverwriteV4(unittest.TestCase):
    def test_later_v3_same_day_does_not_overwrite_v4_estado(self):
        v4, snap = pack_v4_sample()
        v3_late = _v3_later_than_v4()
        # v3 llega después (SENT_MS mayor) el mismo día. El estado lo escribe v4.
        rows = index(V.rows_from_messages(v4 + v3_late, SESSION, True))
        for ccy in CCYS:
            row = rows[("RTF10_G8", ccy)]
            self.assertEqual(row["estado"], "OK")
            self.assertEqual(row["voto"], "NA")
            self.assertEqual(row["version"], "4.0.0")
            self.assertEqual(row["snapshot_id"], snap)
            self.assertNotEqual(row["estado"], "TP_SAFE")

    def test_v3_first_then_v4_still_writes_v4_estado(self):
        v4, snap = pack_v4_sample()
        v3 = v3_mails(_driver_fields(), alert_version="3")
        rows = index(V.rows_from_messages(v3 + v4, SESSION, True))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "OK")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["version"], "4.0.0")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["snapshot_id"], snap)

    def test_v3_only_remains_historical_driver_path(self):
        # Sin v4, el camino histórico no se toca (tests existentes de v3).
        mails = v3_mails(_driver_fields(), alert_version="3")
        rows = index(V.rows_from_messages(mails, SESSION, True))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "TP_SAFE")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["voto"], "NA")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["version"], "3.10.0")



class StaleOnlyFromFirstV4Email(unittest.TestCase):
    """t340u: STALE solo desde la fecha del primer correo v4 (historia, no hardcode)."""

    FIRST = date(2026, 11, 20)  # fecha derivada en el test, no una constante del lector

    def test_history_derives_first_v4_session_not_hardcoded(self):
        history = [
            {"fecha_cierre": "2026-11-18", "script": "RTF10_G8", "version": "3.10.0",
             "estado": "TP_SAFE"},
            {"fecha_cierre": "2026-11-20", "script": "RTF10_G8", "version": "4.0.0",
             "estado": "OK"},
            {"fecha_cierre": "2026-11-21", "script": "RTF10_G8", "version": "3.10.0",
             "estado": "STALE"},
        ]
        first = V.first_rtf10_v4_session_from_rows(history)
        self.assertEqual(first, date(2026, 11, 20))
        # Un STALE con version 3.10.0 no adelanta ni retrasa la fecha.
        self.assertEqual(
            V.first_rtf10_v4_session_from_rows(history + [
                {"fecha_cierre": "2026-11-17", "script": "RTF10_G8",
                 "version": "3.10.0", "estado": "STALE"},
            ]),
            date(2026, 11, 20),
        )
        self.assertIsNone(V.first_rtf10_v4_session_from_rows([
            {"fecha_cierre": "2026-11-18", "script": "RTF10_G8", "version": "3.10.0",
             "estado": "TP_SAFE"},
        ]))

    def test_days_before_first_v4_stay_missing_or_driver(self):
        before = date(2026, 11, 19)
        # Sin correo: MISSING, no STALE.
        rows = index(V.rows_from_messages(
            [], before, True, first_rtf10_v4_session=self.FIRST,
        ))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "MISSING")
        self.assertTrue(all(
            r["estado"] == "MISSING" for r in rows.values() if r["script"] == "RTF10_G8"
        ))
        # Solo v3: DRIVER → TP_SAFE, no STALE.
        v3 = v3_mails(_driver_fields(), alert_version="3")
        # BAR en v3_mails usa SESSION (21-nov). Hay que alinear la barra al día anterior.
        # Usamos rows_from_messages con session=SESSION y first_v4 en el futuro
        # relativo, más un v3 del propio SESSION (fixture), first = día siguiente.
        later_first = date(2026, 11, 22)
        rows = index(V.rows_from_messages(
            v3, SESSION, True, first_rtf10_v4_session=later_first,
        ))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "TP_SAFE")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["voto"], "NA")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["version"], "3.10.0")

    def test_after_first_v4_no_v4_or_only_v3_is_stale(self):
        first = date(2026, 11, 20)
        # Sin correo v4 en un día posterior: STALE, no MISSING.
        rows = index(V.rows_from_messages(
            [], SESSION, True, first_rtf10_v4_session=first,
        ))
        for ccy in CCYS:
            self.assertEqual(rows[("RTF10_G8", ccy)]["estado"], "STALE")
            self.assertEqual(rows[("RTF10_G8", ccy)]["voto"], "NA")
        # Solo v3 después del primer v4: STALE, no DRIVER.
        v3 = v3_mails(_driver_fields(), alert_version="3")
        rows = index(V.rows_from_messages(
            v3, SESSION, True, first_rtf10_v4_session=first,
        ))
        for ccy in CCYS:
            self.assertEqual(rows[("RTF10_G8", ccy)]["estado"], "STALE")
            self.assertEqual(rows[("RTF10_G8", ccy)]["voto"], "NA")
            self.assertNotEqual(rows[("RTF10_G8", ccy)]["estado"], "TP_SAFE")
        # En el primer intento no se escribe DRIVER (append-only no lo puede pisar después).
        early = [r for r in V.rows_from_messages(
            v3, SESSION, False, first_rtf10_v4_session=first,
        ) if r["script"] == "RTF10_G8"]
        self.assertEqual(early, [])

    def test_without_v4_history_existing_paths_unchanged(self):
        # Sin historia v4, los caminos antiguos no se tocan (no hace falta
        # editar test_votes_ingest.py).
        rows = index(V.rows_from_messages([], SESSION, True))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "MISSING")
        v3 = v3_mails(_driver_fields(), alert_version="3")
        rows = index(V.rows_from_messages(v3, SESSION, True))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "TP_SAFE")


if __name__ == "__main__":
    unittest.main()

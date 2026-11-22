"""t337u: el lector acepta RTF10_G8 con ALERT_VERSION=4.

Transporte intacto (G8_PARTS_V1 rev 1.3, MODULE=RTF10_G8). El esquema v4 ya
no trae DRIVER/POL/LER/CONF/BETA. READING no es voto ni señal. v3
(ALERT_VERSION ausente o 3 / SCRIPT_VERSION 3.10.0) sigue parseable, histórico.
Tests nuevos solamente; no se editan tests ni fixtures existentes.
t337u, orden expresa Sander 22-nov.
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

SESSION = date(2026, 11, 21)
BAR = V.expected_bar_close_ms(SESSION)
CCYS = list(V.CCYS)

# Constantes del bloque G8 de RTF10_G8_v4.0.0.pine (líneas 818-820, 966).
G8TX_LIMIT = 3900
G8TX_MAXPARTS = 14
G8TX_OVERHEAD = 70
ENVELOPE = {
    "INSTANCE", "MESSAGE_ID", "EVENT", "HOST", "TF",
    "BAR_CLOSE_MS", "SENT_MS", "SNAPSHOT_ID", "PART", "PARTS",
}

# Campos que emite el bloque G8 del pine (g8_m1, g8_m2, g8_row, g8_f1, eventos).
# Extraídos de RTF10_G8_v4.0.0.pine; no se toca la metodología.
PINE_GLOBALS = (
    "CADENCE", "HOST_CONFIRMED", "BAR_OPEN_MS", "BAR_CLOSE_UTC",
    "METHOD", "IDENTITY", "CCYS", "RAW_SOURCE_TOTAL",
    "COMMON_DATE", "COMMON_DATE_H_START", "HORIZON_BARS", "SHOCK_BARS",
    "MIN_MARKETS", "BAND_BP", "FX_MIN_PCT", "AUX_MIN_BP", "HYSTERESIS",
    "LIQ_PCTILE", "SHOCK_MIN_BP", "CALIBRATION", "FORWARD_TEST",
    "COMMON_H_BP", "COMMON_N", "COMMON_SHORT_H_BP", "COMMON_5D_BP",
    "SYNC_RATIO", "SYNC", "DOWN_N", "UP_N",
    "LIQ", "VIX", "MOVE", "VIX_P", "MOVE_P",
    "USD_FISHER", "USD_D10Y_FRED_H_BP", "USD_BE_H_BP", "USD_REAL_H_BP",
    "USD_REAL5_H_BP", "USD_REAL_SLOPE_H_BP", "USD_FISHER_GAP_BP",
    "USD_ROTATION", "USD_REAL5_OFF", "USD_FRED_DAY_UTC",
    "EUR_FR_DE_H_BP", "EUR_IT_DE_H_BP", "EUR_FRAG", "EURCHF_H_LOG_PCT",
    "NOTE_US_5Y5Y_H_BP", "NOTE_COPPER_GOLD_H_LOG_PCT", "NOTE_KW_H_BP",
    "NOTE_KW_LATE", "INFL_REAL_NON_USD", "NOTES_VOTE", "READING_IS_SIGNAL",
)
PINE_ROW_SUFFIXES = (
    "D10Y_H_BP", "LOCAL_H_BP", "SHORT_H_BP", "SLOPE_REL_H_BP",
    "LOCAL_5D_BP", "SHOCK_5D", "FX_H_LOG_PCT",
    "READING", "READING_MAIN", "LIQ_TAG", "FRAG_TAG",
    "LOCAL_MATERIAL", "BAND_BP", "STATUS", "TIER", "RHO63_REGIME", "FX_BASIS",
)
PINE_VAL = (
    "VAL_US10Y", "VAL_US02Y", "VAL_DE10Y", "VAL_DE02Y", "VAL_GB10Y", "VAL_GB02Y",
    "VAL_JP10Y", "VAL_JP02Y", "VAL_AU10Y", "VAL_AU02Y", "VAL_NZ10Y", "VAL_NZ02Y",
    "VAL_CA10Y", "VAL_CA02Y", "VAL_CH10Y", "VAL_CH02Y", "VAL_FR10Y", "VAL_IT10Y",
    "VAL_EURUSD", "VAL_GBPUSD", "VAL_USDJPY", "VAL_AUDUSD", "VAL_NZDUSD",
    "VAL_USDCAD", "VAL_USDCHF",
    "VAL_DGS10", "VAL_T10YIE", "VAL_DFII10", "VAL_DFII5", "VAL_T5YIFR",
    "VAL_THREEFYTP10", "VAL_HG1", "VAL_GOLD", "VAL_VIX", "VAL_MOVE",
)
# Campos retirados en v4 (orden 22-nov). El lector no los usa para estado/voto.
V4_DISABLED = ("DRIVER", "POL_H_BP", "LER_H_BP", "CONF", "BETA")

# Lecturas de muestra (etiquetas de la orden). No son votos.
READINGS = {
    "USD": ("ATTRACT·LIQ", "ATTRACT", "YES", "NO"),
    "EUR": ("PREMIUM", "PREMIUM", "NO", "NO"),
    "GBP": ("RELIEF", "RELIEF", "NO", "NO"),
    "JPY": ("HAVEN", "HAVEN", "NO", "NO"),
    "AUD": ("—", "—", "NO", "NO"),
    "NZD": ("NA", "NA", "NO", "NO"),
    "CAD": ("NA_FEED", "NA_FEED", "NO", "NO"),
    "CHF": ("ATTRACT·FRAG", "ATTRACT", "NO", "YES"),
}


def _num(v):
    return "%.4f" % v


def g8tx_add(parts, errors, fragment, budget):
    """Empaqueta como g8tx_add del bloque G8 de RTF10_G8_v4.0.0.pine."""
    for field in fragment.split("|"):
        if field == "":
            continue
        eq = field.find("=")
        key = field[:eq] if eq >= 0 else ""
        if key in ENVELOPE:
            continue
        if eq < 0 and parts:
            cont = ";" + field
            if len(parts[-1]) + len(cont) <= budget:
                parts[-1] += cont
            else:
                errors.append("FIELD_TOO_LONG:CONTINUATION")
        elif len(field) + 1 > budget:
            errors.append("FIELD_TOO_LONG:" + key)
        else:
            addition = "|" + field
            if not parts:
                parts.append(addition)
            elif len(parts[-1]) + len(addition) > budget:
                parts.append(addition)
            else:
                parts[-1] += addition
    return len(parts)


def pack_v4_sample(extra=None, changes=None):
    """Paquete de ejemplo construido a partir del bloque G8 del pine."""
    sent = 1764100000000
    inst = "RTF10_MAIN"
    snap = "RTF10_G8:%s:FX:EURUSD:%s:%s" % (inst, BAR, sent)
    header = (
        "G8|SCHEMA=1|MODULE=RTF10_G8|SCRIPT_VERSION=4.0.0|ALERT_VERSION=4"
        "|TRANSPORT=G8_PARTS_V1|TRANSPORT_REV=1.3"
        "|INSTANCE=%s|HOST=FX:EURUSD|TF=1D|BAR_CLOSE_MS=%s"
        "|SENT_MS=%s|EVENT=DAILY_SNAPSHOT|SNAPSHOT_ID=%s"
        % (inst, BAR, sent, snap)
    )
    budget = G8TX_LIMIT - len(header) - len(snap) - G8TX_OVERHEAD
    parts, errors = [], []
    ev = list(changes) if changes is not None else [
        "EUR_READING_CHANGE", "SYNC_CHANGE",
    ]
    g8tx_add(parts, errors, "|CHANGES_N=%s" % len(ev), budget)
    if not ev:
        g8tx_add(parts, errors, "|CHANGES=NONE|CHANGES_PARTS=0", budget)
    else:
        g8tx_add(parts, errors, "|CHANGES=%s|CHANGES_PARTS=1" % ",".join(ev), budget)
    g8_m1 = (
        "|CADENCE=D1_CLOSE|HOST_CONFIRMED=YES|BAR_OPEN_MS=%s"
        "|BAR_CLOSE_UTC=2026-11-21 21:00:00"
        "|METHOD=LEDGER_MEDIAN_G8|IDENTITY=COMMON+SHORT+SLOPE_REL=D10Y"
        "|CCYS=USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF|RAW_SOURCE_TOTAL=35"
        "|COMMON_DATE=20261121|COMMON_DATE_H_START=20260901"
        "|HORIZON_BARS=63|SHOCK_BARS=5|MIN_MARKETS=6"
        "|BAND_BP=%s|FX_MIN_PCT=%s|AUX_MIN_BP=%s|HYSTERESIS=EXIT_AT_HALF"
        "|LIQ_PCTILE=%s|SHOCK_MIN_BP=%s"
        "|CALIBRATION=PREREG_V1_A1_1994_2019"
        "|FORWARD_TEST=2026-11-23_TO_2027-02-23"
        "|COMMON_H_BP=%s|COMMON_N=8|COMMON_SHORT_H_BP=%s|COMMON_5D_BP=%s"
        "|SYNC_RATIO=%s|SYNC=SYNC HIGH|DOWN_N=2|UP_N=6"
        "|LIQ=YES|VIX=%s|MOVE=%s|VIX_P=%s|MOVE_P=%s"
        % (BAR - 86400000, _num(12), _num(0.65), _num(6), _num(90), _num(20),
           _num(12.5), _num(4.0), _num(1.25), _num(0.8750),
           _num(18.5), _num(90.0), _num(0.8000), _num(0.7000))
    )
    g8tx_add(parts, errors, g8_m1, budget)
    g8_m2 = (
        "|USD_FISHER=EXACT|USD_D10Y_FRED_H_BP=%s|USD_BE_H_BP=%s|USD_REAL_H_BP=%s"
        "|USD_REAL5_H_BP=%s|USD_REAL_SLOPE_H_BP=%s|USD_FISHER_GAP_BP=%s"
        "|USD_ROTATION=NO|USD_REAL5_OFF=NO|USD_FRED_DAY_UTC=2026-11-20"
        "|EUR_FR_DE_H_BP=%s|EUR_IT_DE_H_BP=%s|EUR_FRAG=NO|EURCHF_H_LOG_PCT=%s"
        "|NOTE_US_5Y5Y_H_BP=%s|NOTE_COPPER_GOLD_H_LOG_PCT=%s"
        "|NOTE_KW_H_BP=%s|NOTE_KW_LATE=NO"
        "|INFL_REAL_NON_USD=BLIND|NOTES_VOTE=NO|READING_IS_SIGNAL=NO"
        % (_num(10.0), _num(3.0), _num(7.0), _num(5.0), _num(1.0), _num(0.5),
           _num(8.0), _num(12.0), _num(-0.25),
           _num(2.0), _num(0.10), _num(1.5))
    )
    g8tx_add(parts, errors, g8_m2, budget)
    rows = []
    for i, ccy in enumerate(CCYS):
        rd, main, liq, frag = READINGS[ccy]
        loc = 15.0 if ccy in ("USD", "EUR", "CHF") else 3.0
        row = (
            "|{c}_D10Y_H_BP={d10}|{c}_LOCAL_H_BP={loc}|{c}_SHORT_H_BP={sh}"
            "|{c}_SLOPE_REL_H_BP={sl}|{c}_LOCAL_5D_BP={l5}|{c}_SHOCK_5D={shock}"
            "|{c}_FX_H_LOG_PCT={fx}|{c}_READING={rd}|{c}_READING_MAIN={main}"
            "|{c}_LIQ_TAG={liq}|{c}_FRAG_TAG={frag}"
            "|{c}_LOCAL_MATERIAL={mat}|{c}_BAND_BP={band}|{c}_STATUS=FRESH"
            "|{c}_TIER={tier}|{c}_RHO63_REGIME={rho}|{c}_FX_BASIS={basis}"
        ).format(
            c=ccy, d10=_num(20.0 + i), loc=_num(loc), sh=_num(2.0 + i),
            sl=_num(1.0), l5=_num(4.0), shock="NO",
            fx=_num(0.15 * (i + 1)), rd=rd, main=main, liq=liq, frag=frag,
            mat="YES" if abs(loc) >= 12 else "NO", band=_num(12),
            tier="OBS_FISHER_EXACT" if ccy == "USD" else "OBS_REAL_BLIND",
            rho=_num(0.40 + 0.02 * i),
            basis="NEG_EQUAL_WEIGHT_MEAN_7_NOT_DXY" if ccy == "USD"
            else "CCY_STRENGTH_VS_USD",
        )
        rows.append(row)
    # El pine empaqueta las 8 divisas de dos en dos.
    for a, b in zip(rows[0::2], rows[1::2]):
        g8tx_add(parts, errors, a + b, budget)
    vals = "|" + "|".join("%s=%s" % (k, _num(1.0)) for k in PINE_VAL)
    g8tx_add(parts, errors, vals, budget)
    if extra:
        g8tx_add(parts, errors, extra if extra.startswith("|") else "|" + extra, budget)
    if errors:
        raise AssertionError("packer errors: %s" % errors)
    total = len(parts)
    if total == 0 or total > G8TX_MAXPARTS:
        raise AssertionError("PARTS=%s out of range" % total)
    mails = []
    for i, body in enumerate(parts, 1):
        packet = (
            header
            + "|MESSAGE_ID=%s:P%s|PART=%s|PARTS=%s|BATCH_STATUS=DATA" % (snap, i, i, total)
            + body
        )
        mails.append({
            "id": "rtf10-v4-p%s" % i,
            "date": "2026-11-21T21:05:00Z",
            "subject": "Alert: RTF10_G8",
            "body": packet,
        })
    return mails, snap


def v3_mails(fields, alert_version="3", script_version="3.10.0"):
    """Correo histórico v3 (DRIVER). No usa el helper de test_votes_ingest."""
    n = 5
    snap = "RTF10_G8:inst:FX:EURUSD:%s:1000" % BAR
    keys = list(fields.items())
    out = []
    for i in range(1, n + 1):
        chunk = dict(keys[i - 1::n]) if keys else {}
        bits = [
            "G8", "SCHEMA=1", "MODULE=RTF10_G8",
            "SCRIPT_VERSION=%s" % script_version,
            "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=1.3",
            "PART=%s" % i, "PARTS=%s" % n,
            "MESSAGE_ID=%s:P%s" % (snap, i),
            "INSTANCE=i", "HOST=FX:EURUSD", "TF=1D",
            "BAR_CLOSE_MS=%s" % BAR, "SENT_MS=1000",
            "SNAPSHOT_ID=%s" % snap, "BATCH_STATUS=DATA",
        ]
        if alert_version is not None:
            bits.insert(4, "ALERT_VERSION=%s" % alert_version)
        for key, val in chunk.items():
            bits.append("%s=%s" % (key, val))
        out.append({
            "id": "rtf10-v3-p%s" % i,
            "date": "2026-11-21T21:05:00Z",
            "subject": "Alert: RTF10_G8",
            "body": "|".join(bits),
        })
    return out


def merged_from(mails):
    kvs = []
    for m in mails:
        kv = V.parse_payload(m["body"])
        if kv is None:
            raise AssertionError("unparseable: %s" % m["id"])
        kvs.append(kv)
    merged, conflict = V._merge_parts(kvs)
    if conflict:
        raise AssertionError("merge conflict")
    return merged


def index(rows):
    return {(r["script"], r["ccy"]): r for r in rows}


class PackerFromPine(unittest.TestCase):
    def test_sample_fits_transport_and_has_eight_ccy_blocks(self):
        mails, _snap = pack_v4_sample()
        self.assertGreaterEqual(len(mails), 1)
        self.assertLessEqual(len(mails), 14)
        merged = merged_from(mails)
        self.assertEqual(merged["BATCH_STATUS"], "DATA")
        self.assertEqual(merged["PARTS"], str(len(mails)))
        self.assertEqual(int(merged["PARTS"]), len(mails))
        self.assertLessEqual(int(merged["PARTS"]), 14)
        for ccy in CCYS:
            for suffix in PINE_ROW_SUFFIXES:
                self.assertIn("%s_%s" % (ccy, suffix), merged, ccy + "_" + suffix)


class AlertVersion4Accepted(unittest.TestCase):
    def test_reader_accepts_alert_version_4_and_keeps_allowed_fields(self):
        mails, snap = pack_v4_sample()
        rows = index(V.rows_from_messages(mails, SESSION, True))
        merged = merged_from(mails)
        self.assertEqual(merged["ALERT_VERSION"], "4")
        self.assertEqual(merged["SCRIPT_VERSION"], "4.0.0")
        self.assertEqual(merged["MODULE"], "RTF10_G8")
        self.assertEqual(merged["TRANSPORT"], "G8_PARTS_V1")
        self.assertEqual(merged["TRANSPORT_REV"], "1.3")
        self.assertEqual(merged["METHOD"], "LEDGER_MEDIAN_G8")
        self.assertEqual(merged["SYNC"], "SYNC HIGH")
        self.assertEqual(merged["READING_IS_SIGNAL"], "NO")
        self.assertEqual(merged["NOTES_VOTE"], "NO")
        self.assertEqual(merged["INFL_REAL_NON_USD"], "BLIND")
        for key in PINE_GLOBALS:
            self.assertIn(key, merged, key)
        for key in PINE_VAL:
            self.assertIn(key, merged, key)
        self.assertEqual(merged["CHANGES_N"], "2")
        self.assertIn("EUR_READING_CHANGE", merged["CHANGES"])
        for ccy in CCYS:
            row = rows[("RTF10_G8", ccy)]
            self.assertEqual(row["version"], "4.0.0")
            self.assertEqual(row["snapshot_id"], snap)
            # t339u, orden expresa Sander 22-nov: estado de salud, no se deriva de READING
            self.assertEqual(row["estado"], "OK")
            self.assertEqual(row["voto"], "NA")
            self.assertEqual(row["valor"], "")
            self.assertLessEqual(int(row["parts_expected"]), 14)
            self.assertEqual(row["parts_expected"], row["parts_received"])

    def test_v4_does_not_read_disabled_fields_even_if_present(self):
        # Si un correo v4 arrastrara campos retirados, no alimentan estado ni voto.
        extra = "|".join(
            "EUR_%s=TP_FISCAL" % suf if not suf.endswith("BP") and suf != "CONF" and suf != "BETA"
            else "EUR_%s=99" % (suf if suf.endswith("BP") else suf + "_X")
            for suf in ("DRIVER", "POL_H_BP", "LER_H_BP")
        )
        extra += "|EUR_CONF=1|EUR_BETA=0.5"
        mails, _ = pack_v4_sample(extra="|" + extra)
        merged = merged_from(mails)
        self.assertEqual(merged.get("EUR_DRIVER"), "TP_FISCAL")
        rows = index(V.rows_from_messages(mails, SESSION, True))
        eur = rows[("RTF10_G8", "EUR")]
        # t339u, orden expresa Sander 22-nov: estado de salud, no se deriva de READING
        self.assertEqual(eur["estado"], "OK")
        self.assertEqual(eur["voto"], "NA")
        self.assertNotEqual(eur["estado"], "TP_FISCAL")
        self.assertNotEqual(eur["estado"], merged["EUR_READING"])
        self.assertNotEqual(eur["estado"], merged["EUR_READING_MAIN"])

    def test_reading_is_not_a_vote_or_signal(self):
        mails, _ = pack_v4_sample()
        merged = merged_from(mails)
        rows = index(V.rows_from_messages(mails, SESSION, True))
        self.assertEqual(merged["READING_IS_SIGNAL"], "NO")
        self.assertEqual(merged["EUR_READING"], "PREMIUM")
        self.assertEqual(merged["EUR_READING_MAIN"], "PREMIUM")
        self.assertEqual(merged["USD_READING"], "ATTRACT·LIQ")
        self.assertEqual(merged["USD_READING_MAIN"], "ATTRACT")
        self.assertEqual(merged["USD_LIQ_TAG"], "YES")
        self.assertEqual(merged["CHF_FRAG_TAG"], "YES")
        for ccy in CCYS:
            row = rows[("RTF10_G8", ccy)]
            self.assertEqual(row["voto"], "NA")
            # t339u, orden expresa Sander 22-nov: estado de salud, no se deriva de READING
            self.assertEqual(row["estado"], "OK")
            # estado es NA fijo, no un mapeo de READING (que a veces también es NA).
            if merged["%s_READING" % ccy] not in ("NA", "NA_FEED", "—"):
                self.assertNotEqual(row["estado"], merged["%s_READING" % ccy])
            if merged["%s_READING_MAIN" % ccy] not in ("NA", "NA_FEED", "—"):
                self.assertNotEqual(row["estado"], merged["%s_READING_MAIN" % ccy])
        # Las notas viajan en el payload y no votan.
        self.assertIn("NOTE_US_5Y5Y_H_BP", merged)
        self.assertTrue(all(
            r["script"] != "NOTE" and not r["script"].startswith("NOTE_")
            for r in rows.values()
        ))

    def test_vote_and_estado_helpers_never_consume_reading(self):
        # Guardia: READING no entra en estado ni en voto.
        self.assertIn("RTF10_G8", V.ALWAYS_NA)
        vote_src = inspect.getsource(V._vote)
        self.assertNotIn("READING", vote_src)
        estado_src = inspect.getsource(V._estado_valor_from_mail)
        # La rama v4 no lee READING; la histórica sigue en DRIVER.
        self.assertIn("ALERT_VERSION", estado_src)
        self.assertNotRegex(estado_src, r'kv\.get\([^)]*READING')


class V3HistoricalStillParseable(unittest.TestCase):
    def _driver_fields(self):
        out = {}
        for ccy in CCYS:
            out["%s_DRIVER" % ccy] = "TP_SAFE"
            out["%s_STATUS" % ccy] = "FRESH"
        return out

    def test_alert_version_3_still_reads_driver(self):
        mails = v3_mails(self._driver_fields(), alert_version="3")
        rows = index(V.rows_from_messages(mails, SESSION, True))
        eur = rows[("RTF10_G8", "EUR")]
        self.assertEqual(eur["estado"], "TP_SAFE")
        self.assertEqual(eur["voto"], "NA")
        self.assertEqual(eur["valor"], "")
        self.assertEqual(eur["version"], "3.10.0")
        self.assertEqual(eur["parts_expected"], "5")
        self.assertEqual(eur["parts_received"], "5")

    def test_missing_alert_version_keeps_v3_driver_path(self):
        # Fixtures existentes no mandan ALERT_VERSION; deben seguir igual.
        mails = v3_mails(self._driver_fields(), alert_version=None)
        merged = merged_from(mails)
        self.assertNotIn("ALERT_VERSION", merged)
        self.assertEqual(merged["SCRIPT_VERSION"], "3.10.0")
        rows = index(V.rows_from_messages(mails, SESSION, True))
        self.assertEqual(rows[("RTF10_G8", "EUR")]["estado"], "TP_SAFE")
        self.assertEqual(rows[("RTF10_G8", "EUR")]["voto"], "NA")


class CatalogUntouched(unittest.TestCase):
    def test_catalog_stays_historical_reference(self):
        # El catálogo no se cambia: referencia histórica v3. Las filas usan
        # SCRIPT_VERSION y PARTS declarados en el correo.
        self.assertEqual(V.MODULE["RTF10_G8"]["version"], "3.10.0")
        self.assertEqual(V.MODULE["RTF10_G8"]["parts"], 5)
        self.assertFalse(V.MODULE["RTF10_G8"]["monolithic"])


if __name__ == "__main__":
    unittest.main()

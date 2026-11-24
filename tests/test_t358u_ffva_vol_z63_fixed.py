"""t358u: VOL z63 de FFVA validado (CONFIG_KEY v1.6.0/a3, vol~63/1/4 sin [PROV]).

El lector acepta esa clave como parámetros fijos en las 8 divisas.
La misma clave con [PROV], u otra versión, se deja como hasta ahora.
VPART sigue sin voto. Fixtures sintéticos: sin cuerpos, asuntos ni
Message-IDs reales, y sin datos de CME ni derivados.
orden Sander 24-nov (t358u).
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import votes_ingest as V  # noqa: E402

SESSION = date(2026, 11, 24)
BAR = V.expected_bar_close_ms(SESSION)
CCYS = list(V.CCYS)

# Solo la firma validada. Sin el resto de tokens de un correo real.
FIXED_KEY = "v1.6.0/a3;vol~63/1/4"
PROV_KEY = "v1.6.0/a3;vol~63/1/4[PROV]"
OTHER_VERSION_KEY = "v1.6.0/a2;vol~63/1/4"

# Etiquetas ASCII sintéticas (no son lecturas de mercado).
# Conflicto deliberado con el régimen: si VPART votara, el signo cambiaría.
VPART = {
    "USD": "DIST.VMINUS",
    "EUR": "DIST.VMINUS",
    "GBP": "FLAT.VEQ",
    "JPY": "ACCUM.VPLUS",
    "AUD": "LIQ.VMINUS",
    "NZD": "SC.VPLUS",
    "CAD": "STALE",
    "CHF": "ROLL",
}
REGIME = {
    "USD": "ACCUM",
    "EUR": "ACCUM",
    "GBP": "DIST",
    "JPY": "FLAT_OI",
    "AUD": "SC",
    "NZD": "LIQ",
    "CAD": "ACCUM",
    "CHF": "DIST",
}
VOTE_FROM_REGIME = {
    "ACCUM": "+1",
    "SC": "+1",
    "DIST": "-1",
    "LIQ": "-1",
    "FLAT_OI": "0",
}


def _synth_mail(config_key, fields, part=1, parts=5, sent=1000):
    snap = "SYNTH_FFVA_T358U:inst:SYNTH:%s:%s" % (bar_or(), sent)
    bits = [
        "G8", "SCHEMA=1", "MODULE=FFVA_G8", "SCRIPT_VERSION=1.6.0",
        "TRANSPORT=G8_PARTS_V1", "TRANSPORT_REV=1.2",
        "INSTANCE=SYNTH_T358U", "HOST=SYNTH:PAIR", "TF=1D",
        "BAR_CLOSE_MS=%s" % bar_or(), "SENT_MS=%s" % sent,
        "SNAPSHOT_ID=%s" % snap, "PART=%s" % part, "PARTS=%s" % parts,
        "MESSAGE_ID=%s:P%s" % (snap, part), "BATCH_STATUS=DATA",
        "CONFIG_KEY=%s" % config_key,
    ]
    for key, val in fields.items():
        bits.append("%s=%s" % (key, val))
    return {
        "id": "synth-t358u-ffva-p%s-%s" % (part, sent),
        "date": "2026-11-24T03:10:00Z",
        "subject": "Alert: SYNTH_FFVA_T358U",
        "body": "|".join(bits),
    }


def bar_or():
    return BAR


def _fields_for_all_ccys():
    out = {}
    for ccy in CCYS:
        out["%s_REGIME" % ccy] = REGIME[ccy]
        out["%s_STATUS" % ccy] = "FRESH"
        out["%s_ROLL" % ccy] = "NO"
        out["%s_VPART" % ccy] = VPART[ccy]
    return out


def _complete(config_key):
    fields = _fields_for_all_ccys()
    keys = list(fields.items())
    n = 5
    msgs = []
    for i in range(1, n + 1):
        chunk = dict(keys[i - 1::n])
        msgs.append(_synth_mail(config_key, chunk, part=i, parts=n))
    return msgs


def _index(rows):
    return {(r["script"], r["ccy"]): r for r in rows}


class FfvaVolZ63Fixed(unittest.TestCase):
    def test_accepted_config_is_fixed_for_all_eight_ccys(self):
        kv = V.parse_payload(_complete(FIXED_KEY)[0]["body"])
        self.assertEqual(kv.get("CONFIG_KEY"), FIXED_KEY)
        self.assertTrue(V.ffva_vol_params_fixed(kv))
        self.assertTrue(V.ffva_vol_params_fixed(FIXED_KEY))
        for ccy in CCYS:
            self.assertTrue(V.ffva_vol_params_fixed(kv, ccy), ccy)
        self.assertEqual(tuple(V.FFVA_VOL_FIXED_CCYS), tuple(CCYS))
        rows = _index(V.rows_from_messages(_complete(FIXED_KEY), SESSION, False))
        self.assertEqual(len([k for k in rows if k[0] == "FFVA_G8"]), 8)
        for ccy in CCYS:
            row = rows[("FFVA_G8", ccy)]
            self.assertEqual(row["estado"], REGIME[ccy], ccy)
            self.assertEqual(row["voto"], VOTE_FROM_REGIME[REGIME[ccy]], ccy)
            self.assertEqual(row["stale"], "false", ccy)
            self.assertEqual(row["valor"], "", ccy)

    def test_same_config_with_prov_is_not_fixed(self):
        kv = {"CONFIG_KEY": PROV_KEY}
        self.assertFalse(V.ffva_vol_params_fixed(kv))
        self.assertFalse(V.ffva_vol_params_fixed(PROV_KEY))
        for ccy in CCYS:
            self.assertFalse(V.ffva_vol_params_fixed(kv, ccy), ccy)
        # El voto sigue saliendo del régimen, como hasta ahora.
        rows = _index(V.rows_from_messages(_complete(PROV_KEY), SESSION, False))
        for ccy in CCYS:
            row = rows[("FFVA_G8", ccy)]
            self.assertEqual(row["estado"], REGIME[ccy], ccy)
            self.assertEqual(row["voto"], VOTE_FROM_REGIME[REGIME[ccy]], ccy)
            self.assertEqual(row["valor"], "", ccy)

    def test_different_version_is_not_fixed(self):
        kv = {"CONFIG_KEY": OTHER_VERSION_KEY}
        self.assertFalse(V.ffva_vol_params_fixed(kv))
        self.assertFalse(V.ffva_vol_params_fixed(OTHER_VERSION_KEY))
        # v1.6.0/a3 sin el token vol~63/1/4 tampoco entra.
        self.assertFalse(V.ffva_vol_params_fixed("v1.6.0/a3;use_2oi~true"))
        # Misma especificación vol, versión de catálogo anterior: no es fija.
        self.assertFalse(V.ffva_vol_params_fixed("v1.5.0/a1;vol~63/1/4"))
        rows = _index(V.rows_from_messages(_complete(OTHER_VERSION_KEY), SESSION, False))
        for ccy in CCYS:
            row = rows[("FFVA_G8", ccy)]
            self.assertEqual(row["estado"], REGIME[ccy], ccy)
            self.assertEqual(row["voto"], VOTE_FROM_REGIME[REGIME[ccy]], ccy)
            self.assertEqual(row["valor"], "", ccy)

    def test_vpart_does_not_vote(self):
        # Con la clave aceptada, VPART sigue fuera del voto.
        rows = _index(V.rows_from_messages(_complete(FIXED_KEY), SESSION, False))
        for ccy in CCYS:
            row = rows[("FFVA_G8", ccy)]
            expected = VOTE_FROM_REGIME[REGIME[ccy]]
            self.assertEqual(row["voto"], expected, ccy)
            self.assertNotEqual(row["voto"], VPART[ccy], ccy)
            self.assertNotIn("VPART", row["voto"])
            self.assertNotIn("VPLUS", row["voto"])
            self.assertNotIn("VMINUS", row["voto"])
            self.assertEqual(row["valor"], "", ccy)
        # Sin CONFIG_KEY (comportamiento previo): VPART tampoco vota.
        fields = _fields_for_all_ccys()
        keys = list(fields.items())
        msgs = []
        for i in range(1, 6):
            chunk = dict(keys[i - 1::5])
            body_mail = _synth_mail(FIXED_KEY, chunk, part=i, parts=5)
            bits = [b for b in body_mail["body"].split("|") if not b.startswith("CONFIG_KEY=")]
            body_mail["body"] = "|".join(bits)
            msgs.append(body_mail)
        bare = _index(V.rows_from_messages(msgs, SESSION, False))
        for ccy in CCYS:
            self.assertEqual(
                bare[("FFVA_G8", ccy)]["voto"],
                VOTE_FROM_REGIME[REGIME[ccy]],
                ccy,
            )
            self.assertFalse(V.ffva_vol_params_fixed(V.parse_payload(msgs[0]["body"])))

    def test_transition_accepts_a2_prov_and_a3_and_vpart_does_not_vote(self):
        # orden Sander 24-nov t359u: transición a2[PROV]/a3
        a2_key = "v1.6.0/a2;vol~63/1/4[PROV]"
        a3_key = "v1.6.0/a3;vol~63/1/4"
        self.assertTrue(V.ffva_vol_params_fixed(a2_key))
        self.assertTrue(V.ffva_vol_params_fixed(a3_key))
        for ccy in CCYS:
            self.assertTrue(V.ffva_vol_params_fixed({"CONFIG_KEY": a2_key}, ccy), ccy)
            self.assertTrue(V.ffva_vol_params_fixed({"CONFIG_KEY": a3_key}, ccy), ccy)
        # Cualquier otra combinación sigue sin aceptarse como fija.
        self.assertFalse(V.ffva_vol_params_fixed("v1.6.0/a2;vol~63/1/4"))
        self.assertFalse(V.ffva_vol_params_fixed("v1.6.0/a3;vol~63/1/4[PROV]"))
        self.assertFalse(V.ffva_vol_params_fixed("v1.6.0/a2;use_2oi~true"))
        for config_key in (a2_key, a3_key):
            rows = _index(V.rows_from_messages(_complete(config_key), SESSION, False))
            self.assertEqual(len([k for k in rows if k[0] == "FFVA_G8"]), 8, config_key)
            for ccy in CCYS:
                row = rows[("FFVA_G8", ccy)]
                expected = VOTE_FROM_REGIME[REGIME[ccy]]
                self.assertEqual(row["voto"], expected, (config_key, ccy))
                self.assertEqual(row["estado"], REGIME[ccy], (config_key, ccy))
                self.assertNotEqual(row["voto"], VPART[ccy], (config_key, ccy))
                self.assertNotIn("VPART", row["voto"])
                self.assertNotIn("VPLUS", row["voto"])
                self.assertNotIn("VMINUS", row["voto"])
                self.assertEqual(row["valor"], "", (config_key, ccy))


if __name__ == "__main__":
    unittest.main()

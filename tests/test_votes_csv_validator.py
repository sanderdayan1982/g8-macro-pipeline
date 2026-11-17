"""Orden Sander 17-nov t240u tarea 5. Validador de data/votes.csv.

CSV sintéticos: el carácter «1», sin cifras de mercado y sin ids de correo.
No reescribe data/votes.csv. Si el fichero publicado incumple, el test queda
rojo y el mensaje lista fecha_cierre, script, ccy y la regla.

Convención del lector para «sin voto»: el token NA. Lo fija
docs/VOTES_RULES_v1.md («NA es ausencia de voto») y
votes_ingest.missing_rows, que escribe voto NA. Una celda vacía no es esa
convención.
"""
import csv
import io
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "tools"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import votes_ingest as V  # noqa: E402
import votes_csv_validate as C  # noqa: E402
from test_allowed_valor_modules import FUSION  # noqa: E402

VOTES = os.path.join(ROOT, "data", "votes.csv")
VALIDATOR = os.path.join(ROOT, "scripts", "tools", "votes_csv_validate.py")

COLS = [
    "fecha_cierre", "script", "ccy", "estado", "valor", "voto", "report_date",
]
ALLOWED = frozenset({"E21"})
BEFORE = "2020-01-02"


def _text(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in columns})
    return buf.getvalue()


def _row(**kwargs):
    base = {col: "" for col in COLS}
    base.update(kwargs)
    return base


def _check(text, columns=None):
    return C.validate_text(
        text,
        columns if columns is not None else COLS,
        ALLOWED,
        FUSION,
    )


def _ids(findings):
    return [
        (f.fecha_cierre, f.script, f.ccy, f.rule, f.line, f.detail)
        for f in findings
    ]


class SyntheticVotesCsv(unittest.TestCase):
    def test_valid_file_is_clean(self):
        rows = [
            _row(fecha_cierre=BEFORE, script="PSI_G8", ccy="EUR",
                 estado="FRESH", valor="1", voto="NA"),
            _row(fecha_cierre=FUSION, script="E21", ccy="EUR",
                 estado="FRESH", valor="1", voto="+1"),
            _row(fecha_cierre=FUSION, script="PSI_G8", ccy="EUR",
                 estado="FRESH", valor="", voto="NA"),
            _row(fecha_cierre=FUSION, script="PSI_G8", ccy="GBP",
                 estado="MISSING", voto="NA"),
            _row(fecha_cierre=FUSION, script="CURVA_G8", ccy="EUR",
                 estado="FRESH", voto="NA", report_date="2020-01-05"),
            _row(fecha_cierre=FUSION, script="CURVA_G8", ccy="GBP",
                 estado="FRESH", voto="NA", report_date=""),
        ]
        self.assertEqual(_check(_text(COLS, rows)), [])

    def test_header_order_and_names(self):
        swapped = ["script", "fecha_cierre", "ccy", "estado", "valor", "voto", "report_date"]
        text = _text(swapped, [_row(fecha_cierre=FUSION, script="PSI_G8", ccy="EUR",
                                    estado="MISSING", voto="NA")])
        self.assertEqual(
            _ids(_check(text)),
            [("", "", "", C.RULE_SCHEMA, 1, "header")],
        )

    def test_header_missing_or_extra_column(self):
        short = [col for col in COLS if col != "voto"]
        self.assertIn(
            ("", "", "", C.RULE_SCHEMA, 1, "header"),
            _ids(_check(_text(short, []))),
        )
        long = COLS + ["notes"]
        self.assertIn(
            ("", "", "", C.RULE_SCHEMA, 1, "header"),
            _ids(_check(_text(long, []))),
        )

    def test_report_date_must_be_last_when_present(self):
        misplaced = [
            "fecha_cierre", "script", "ccy", "estado", "valor", "voto",
            "report_date", "session_type",
        ]
        text = "fecha_cierre,script,ccy,estado,valor,voto,report_date,session_type\n"
        findings = _check(text, misplaced)
        self.assertEqual(
            [f.detail for f in findings if f.rule == C.RULE_SCHEMA],
            ["report_date_position"],
        )
        file_middle = (
            "fecha_cierre,script,ccy,estado,valor,voto,report_date,extra\n"
            "%s,PSI_G8,EUR,MISSING,,NA,,\n" % FUSION
        )
        details = [f.detail for f in _check(file_middle) if f.rule == C.RULE_SCHEMA]
        self.assertEqual(details, ["header", "report_date_position"])

    def test_field_count_is_schema(self):
        text = _text(COLS, []) + "%s,PSI_G8,EUR,MISSING\n" % FUSION
        self.assertEqual(
            _ids(_check(text)),
            [(FUSION, "PSI_G8", "EUR", C.RULE_SCHEMA, 2, "field_count")],
        )

    def test_one_writer_per_key(self):
        rows = [
            _row(fecha_cierre=FUSION, script="PSI_G8", ccy="EUR", estado="FRESH", voto="NA"),
            _row(fecha_cierre=FUSION, script="PSI_G8", ccy="EUR", estado="STALE", voto="NA"),
            _row(fecha_cierre=FUSION, script="PSI_G8", ccy="GBP", estado="FRESH", voto="NA"),
        ]
        self.assertEqual(
            _ids(_check(_text(COLS, rows))),
            [
                (FUSION, "PSI_G8", "EUR", C.RULE_ONE_WRITER, 2, ""),
                (FUSION, "PSI_G8", "EUR", C.RULE_ONE_WRITER, 3, ""),
            ],
        )

    def test_duplicate_rows_are_listed_with_the_second_writer(self):
        row = _row(fecha_cierre=FUSION, script="PSI_G8", ccy="EUR",
                   estado="MISSING", voto="NA")
        findings = _check(_text(COLS, [row, row]))
        self.assertEqual(
            _ids(findings),
            [
                (FUSION, "PSI_G8", "EUR", C.RULE_ONE_WRITER, 2, ""),
                (FUSION, "PSI_G8", "EUR", C.RULE_DUPLICATE_ROW, 2, ""),
                (FUSION, "PSI_G8", "EUR", C.RULE_ONE_WRITER, 3, ""),
                (FUSION, "PSI_G8", "EUR", C.RULE_DUPLICATE_ROW, 3, ""),
            ],
        )
        self.assertNotIn("NA", findings[0].public())

    def test_valor_outside_the_whitelist_uses_the_same_cutoff(self):
        early = _row(fecha_cierre=BEFORE, script="PSI_G8", ccy="EUR",
                     estado="FRESH", valor="1", voto="NA")
        on_cut = _row(fecha_cierre=FUSION, script="PSI_G8", ccy="GBP",
                      estado="FRESH", valor="1", voto="NA")
        allowed = _row(fecha_cierre=FUSION, script="E21", ccy="EUR",
                       estado="FRESH", valor="1", voto="+1")
        findings = _check(_text(COLS, [early, on_cut, allowed]))
        self.assertEqual(
            _ids(findings),
            [(FUSION, "PSI_G8", "GBP", C.RULE_VALOR, 3, "")],
        )
        leaked = _row(fecha_cierre=FUSION, script="PSI_G8", ccy="CAD",
                      estado="FRESH", valor="PROBE", voto="NA")
        shown = _check(_text(COLS, [leaked]))[0].public()
        self.assertNotIn("PROBE", shown)

    def test_published_whitelist_object_is_not_copied(self):
        text = _text(COLS, [_row(
            fecha_cierre=FUSION, script="E21", ccy="EUR",
            estado="FRESH", valor="1", voto="+1",
        )])
        passed = V.ALLOWED_VALOR_MODULES
        findings = C.validate_text(text, COLS, passed, FUSION)
        self.assertEqual(findings, [])
        self.assertIs(passed, V.ALLOWED_VALOR_MODULES)

    def test_missing_vote_is_na_not_empty(self):
        """NA is the reader's absence. Empty and a real vote both fail."""
        self.assertEqual(C.ABSENT_VOTE, "NA")
        ok = _row(fecha_cierre=FUSION, script="PSI_G8", ccy="EUR",
                  estado="MISSING", voto="NA")
        self.assertEqual(_check(_text(COLS, [ok])), [])
        cases = (
            ("", "EUR"),
            ("+1", "GBP"),
            ("0", "JPY"),
            ("-1", "AUD"),
        )
        rows = [
            _row(fecha_cierre=FUSION, script="PSI_G8", ccy=ccy,
                 estado="MISSING", voto=voto)
            for voto, ccy in cases
        ]
        findings = _check(_text(COLS, rows))
        self.assertEqual(
            [(f.ccy, f.rule) for f in findings],
            [(ccy, C.RULE_MISSING) for _voto, ccy in cases],
        )
        for finding in findings:
            self.assertNotIn("voto", finding.public())
            self.assertNotIn("valor", finding.public())

    def test_fresh_row_may_carry_a_vote(self):
        row = _row(fecha_cierre=FUSION, script="POL_G8", ccy="EUR",
                   estado="FRESH", voto="+1")
        self.assertEqual(_check(_text(COLS, [row])), [])

    def test_report_date_equal_to_fecha_cierre_fails(self):
        copied = _row(fecha_cierre=FUSION, script="CURVA_G8", ccy="EUR",
                      estado="FRESH", voto="NA", report_date=FUSION)
        other = _row(fecha_cierre=FUSION, script="CURVA_G8", ccy="GBP",
                     estado="FRESH", voto="NA", report_date="2020-01-05")
        blank = _row(fecha_cierre=FUSION, script="CURVA_G8", ccy="JPY",
                     estado="FRESH", voto="NA", report_date="")
        findings = _check(_text(COLS, [copied, other, blank]))
        self.assertEqual(
            _ids(findings),
            [(FUSION, "CURVA_G8", "EUR", C.RULE_REPORT_DATE, 2, "")],
        )

    def test_report_date_rule_is_skipped_when_the_column_is_absent(self):
        columns = [col for col in COLS if col != "report_date"]
        row = {col: "" for col in columns}
        row.update({
            "fecha_cierre": FUSION, "script": "PSI_G8", "ccy": "EUR",
            "estado": "MISSING", "voto": "NA",
        })
        self.assertEqual(_check(_text(columns, [row]), columns), [])

    def test_writer_columns_accept_one_synthetic_row(self):
        row = {col: "" for col in V.COLUMNS}
        row.update({
            "fecha_cierre": FUSION,
            "script": "PSI_G8",
            "ccy": "EUR",
            "estado": "MISSING",
            "voto": "NA",
        })
        findings = C.validate_text(_text(V.COLUMNS, [row]), V.COLUMNS, V.ALLOWED_VALOR_MODULES, FUSION)
        self.assertEqual(findings, [])
        if "report_date" in V.COLUMNS:
            self.assertEqual(V.COLUMNS[-1], "report_date")

    def test_validator_source_does_not_copy_whitelist_or_cutoff(self):
        with open(VALIDATOR, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn(FUSION, src)
        self.assertNotIn("E21", src)
        self.assertNotIn("ALLOWED_VALOR_MODULES =", src)


class PublishedVotesCsv(unittest.TestCase):
    def test_published_votes_csv(self):
        findings = C.validate_path(
            VOTES,
            columns=V.COLUMNS,
            allowed_valor=V.ALLOWED_VALOR_MODULES,
            valor_cutoff=FUSION,
        )
        self.assertEqual(
            [(f.fecha_cierre, f.script, f.ccy, f.rule) for f in findings],
            [],
            "\n".join(f.public() for f in findings),
        )


if __name__ == "__main__":
    unittest.main()

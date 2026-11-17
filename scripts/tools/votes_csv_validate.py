#!/usr/bin/env python3
"""Validate a votes.csv file. Orden Sander 17-nov t240u tarea 5.

The check does not rewrite the file and does not decide a vote. A finding
names fecha_cierre, script, ccy and the rule. It does not carry valor, voto,
or a mail id.

Rules
-----
schema
    The header is the writer's column list, in that order
    (``votes_ingest.COLUMNS`` for the published file). When that list or the
    file contains ``report_date``, the name is the last column. A data row
    whose field count differs from the header is also ``schema``.
one_writer
    One row per (fecha_cierre, script, ccy). A second row is a second writer
    of the same key. Every row of a repeated key is listed.
duplicate_row
    No two data rows are identical across every column. Every copy is listed.
valor_outside_whitelist
    On and after the cutoff, ``valor`` is empty unless ``script`` is in the
    whitelist. The cutoff and the whitelist are arguments: the published
    check passes ``test_allowed_valor_modules.FUSION`` and
    ``votes_ingest.ALLOWED_VALOR_MODULES``. Rows with ``fecha_cierre``
    strictly before the cutoff are not judged, same comparison as that test.
missing_has_vote
    A row with ``estado`` MISSING carries no vote. The reader
    (``docs/VOTES_RULES_v1.md``: "NA es ausencia de voto", and
    ``votes_ingest.missing_rows``, which writes ``voto`` NA) uses the token
    NA for that absence. An empty cell is not the reader's convention, so
    empty, ``+1``, ``0`` and ``-1`` all fail.
report_date_copied
    When the column exists, a non-empty ``report_date`` that equals
    ``fecha_cierre`` is a copy and fails. An empty cell is not a copy.
    The rule is skipped when the file has no ``report_date`` column.
"""
from __future__ import annotations

import csv
import io
import os
import sys
from collections import Counter
from typing import NamedTuple

# The votes.csv reader keeps the cell it was given. Absence of a vote is the
# token NA, not an empty cell. docs/VOTES_RULES_v1.md and missing_rows().
ABSENT_VOTE = "NA"

RULE_SCHEMA = "schema"
RULE_ONE_WRITER = "one_writer"
RULE_DUPLICATE_ROW = "duplicate_row"
RULE_VALOR = "valor_outside_whitelist"
RULE_MISSING = "missing_has_vote"
RULE_REPORT_DATE = "report_date_copied"

_KEY = ("fecha_cierre", "script", "ccy")


class Finding(NamedTuple):
    fecha_cierre: str
    script: str
    ccy: str
    rule: str
    line: int
    detail: str = ""

    def public(self):
        """Row identity and rule. No cell values, no mail ids."""
        text = "fecha_cierre=%s script=%s ccy=%s rule=%s line=%s" % (
            self.fecha_cierre, self.script, self.ccy, self.rule, self.line,
        )
        if self.detail:
            text += " detail=%s" % self.detail
        return text


def _report_date_not_last(names):
    return "report_date" in names and names[-1] != "report_date"


def _key_of(header, fields):
    out = []
    for name in _KEY:
        if name not in header:
            out.append("")
            continue
        index = header.index(name)
        out.append(fields[index] if index < len(fields) else "")
    return tuple(out)


def _row_map(header, fields):
    row = {}
    for index, name in enumerate(header):
        if name not in row:
            row[name] = fields[index]
    return row


def validate_text(text, columns, allowed_valor, valor_cutoff):
    """Return findings for a whole CSV document.

    ``columns`` is the expected header (the writer's list). ``allowed_valor``
    is the whitelist object the caller already holds. ``valor_cutoff`` is the
    same date string the valor test uses; this module does not store a copy.
    """
    expected = list(columns)
    findings = []
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        findings.append(Finding("", "", "", RULE_SCHEMA, 1, "header"))
        return findings
    header = list(header)
    if header != expected:
        findings.append(Finding("", "", "", RULE_SCHEMA, 1, "header"))
    if _report_date_not_last(expected) or _report_date_not_last(header):
        findings.append(Finding("", "", "", RULE_SCHEMA, 1, "report_date_position"))

    parsed = []
    has_key = all(name in header for name in _KEY)
    for line, fields in enumerate(reader, start=2):
        if len(fields) != len(header):
            fecha, script, ccy = _key_of(header, fields)
            findings.append(Finding(fecha, script, ccy, RULE_SCHEMA, line, "field_count"))
            continue
        parsed.append((line, _row_map(header, fields), tuple(fields)))

    key_counts = Counter()
    full_counts = Counter()
    if has_key:
        for _line, row, _full in parsed:
            key_counts[(row.get("fecha_cierre") or "", row.get("script") or "", row.get("ccy") or "")] += 1
    for _line, _row, full in parsed:
        full_counts[full] += 1

    judge_valor = "valor" in header and "script" in header and "fecha_cierre" in header
    judge_missing = "estado" in header and "voto" in header
    judge_report = "report_date" in header

    for line, row, full in parsed:
        fecha = row.get("fecha_cierre") or ""
        script = row.get("script") or ""
        ccy = row.get("ccy") or ""
        if has_key and key_counts[(fecha, script, ccy)] > 1:
            findings.append(Finding(fecha, script, ccy, RULE_ONE_WRITER, line))
        if full_counts[full] > 1:
            findings.append(Finding(fecha, script, ccy, RULE_DUPLICATE_ROW, line))
        if judge_valor and not (fecha < valor_cutoff) and script not in allowed_valor:
            if (row.get("valor") or "") != "":
                findings.append(Finding(fecha, script, ccy, RULE_VALOR, line))
        if judge_missing and (row.get("estado") or "") == "MISSING":
            if (row.get("voto") or "") != ABSENT_VOTE:
                findings.append(Finding(fecha, script, ccy, RULE_MISSING, line))
        if judge_report:
            report_date = row.get("report_date") or ""
            if report_date != "" and report_date == fecha:
                findings.append(Finding(fecha, script, ccy, RULE_REPORT_DATE, line))
    return findings


def validate_path(path, columns, allowed_valor, valor_cutoff):
    with open(path, encoding="utf-8", newline="") as fh:
        return validate_text(fh.read(), columns, allowed_valor, valor_cutoff)


def main(argv):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for entry in (os.path.join(root, "scripts"), os.path.join(root, "tests")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    import votes_ingest as votes
    from test_allowed_valor_modules import FUSION

    path = argv[1] if len(argv) > 1 else os.path.join(root, "data", "votes.csv")
    findings = validate_path(
        path,
        columns=votes.COLUMNS,
        allowed_valor=votes.ALLOWED_VALOR_MODULES,
        valor_cutoff=FUSION,
    )
    for finding in findings:
        print(finding.public())
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

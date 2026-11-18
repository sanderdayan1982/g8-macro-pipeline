#!/usr/bin/env python3
"""Append-only merge for data/votes.csv. Orden ORDEN_ENZO_REVISION-TRIPLE-01 bloque 2.

git_push_retry.sh registers this as the merge driver for data/votes.csv so
`-X theirs` does not replace the file. During a rebase, %A is main and %B is
the commit being replayed. Main's bytes stay a prefix. Rows in %B whose key
(fecha_cierre, script, ccy) is absent from %A are appended, in that order.
"""
from __future__ import annotations

import csv
import io
import sys


def _rows(raw):
    if not raw:
        return [], []
    text = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    header = list(reader.fieldnames or [])
    rows = []
    for row in reader:
        if not row:
            continue
        key = (
            (row.get("fecha_cierre") or "").strip(),
            (row.get("script") or "").strip(),
            (row.get("ccy") or "").strip(),
        )
        if not all(key):
            continue
        rows.append((key, row))
    return header, rows


def _cell(row, col):
    """report_date is copied only when the other side already has it.

    A weekly module's publication date is never estimated in this merge.
    """
    return row.get(col) or ""


def merge_votes_bytes(main_bytes, other_bytes):
    """Return main_bytes plus rows from other_bytes whose key main does not have.

    main_bytes is a prefix of the result whenever it is empty or already ends
    in a newline. A missing trailing newline is the only byte inserted into
    that prefix, matching scripts/votes_ingest.py append_rows.
    """
    header, have_rows = _rows(main_bytes)
    have = {key for key, _row in have_rows}
    other_header, other_rows = _rows(other_bytes)
    if not header:
        header = other_header
    missing = []
    for key, row in other_rows:
        if key in have:
            continue
        have.add(key)
        missing.append(row)
    if not missing or not header:
        return main_bytes
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n", extrasaction="ignore")
    for row in missing:
        writer.writerow({col: _cell(row, col) for col in header})
    extra = buf.getvalue().encode("utf-8")
    if not extra:
        return main_bytes
    out = main_bytes
    if out and not out.endswith(b"\n"):
        out += b"\n"
    return out + extra


def main(argv):
    if len(argv) != 4:
        print("usage: votes_csv_merge.py ancestor current other", file=sys.stderr)
        return 2
    current, other = argv[2], argv[3]
    with open(current, "rb") as fh:
        main_bytes = fh.read()
    with open(other, "rb") as fh:
        other_bytes = fh.read()
    merged = merge_votes_bytes(main_bytes, other_bytes)
    with open(current, "wb") as fh:
        fh.write(merged)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

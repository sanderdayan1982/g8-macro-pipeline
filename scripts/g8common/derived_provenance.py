"""Procedencia de series derivadas (t347u).

Cada derivado guarda el sha y la fecha de cada entrada, y su generated_utc.
No calcula el valor de la serie; solo registra con qué entradas se generó.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone

SCHEMA = "G8_DERIVED_PROVENANCE/1"
SUBDIR = os.path.join("data", "_ingest", "derived_provenance")
UTC = timezone.utc


def _parse_date(s):
    s = (s or "").strip().replace("-", "")[:8]
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
        except ValueError:
            return None
    return None


def sha256_path(path):
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def valued_dates_from_bytes(data):
    """Fechas de observación con al menos un valor no vacío (no cuenta filas en blanco)."""
    if data is None:
        return set()
    out = set()
    for line in data.decode("utf-8", errors="ignore").splitlines():
        parts = line.split(",")
        d = _parse_date(parts[0] if parts else "")
        if d and any(p.strip() for p in parts[1:]):
            out.add(d)
    return out


def last_valued_date(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        dates = valued_dates_from_bytes(fh.read())
    return max(dates).isoformat() if dates else None


def path_for(root, output_file):
    """Ruta del sidecar JSON. *root* es la raíz del repo (el padre de data/)."""
    base = os.path.basename(output_file)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return os.path.join(root, SUBDIR, stem + ".json")


def path_for_data_dir(data_dir, output_file):
    base = os.path.basename(output_file)
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return os.path.join(data_dir, "_ingest", "derived_provenance", stem + ".json")


def _normalize(rec):
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return None
    if not rec.get("generated_utc") or not isinstance(rec.get("inputs"), dict):
        return None
    inputs = {}
    for name, item in rec["inputs"].items():
        if not isinstance(item, dict):
            continue
        sha = item.get("sha") or item.get("sha256")
        dt = item.get("date")
        if not sha or not dt:
            return None
        inputs[name] = {"sha": sha, "date": dt}
    if not inputs:
        return None
    return {
        "schema": SCHEMA,
        "file": rec.get("file") or "",
        "generated_utc": rec["generated_utc"],
        "inputs": inputs,
    }


def read_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return _normalize(json.load(fh))
    except (OSError, ValueError, TypeError):
        return None


def read(root, output_file):
    return read_file(path_for(root, output_file))


def read_bytes(data):
    try:
        return _normalize(json.loads(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data))
    except (ValueError, TypeError, AttributeError):
        return None


def _input_path(data_dir, name):
    name = name.replace("\\", "/")
    if name.startswith("data/"):
        # data_dir is the data folder; strip the prefix
        return os.path.join(os.path.dirname(data_dir), name)
    return os.path.join(data_dir, os.path.basename(name))


def write_for_data_dir(data_dir, output_file, input_files, generated_utc=None):
    """Escribe el sidecar junto a data/_ingest/derived_provenance/. No toca la salida derivada."""
    generated_utc = generated_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    inputs = {}
    for name in input_files:
        path = _input_path(data_dir, name)
        sha = sha256_path(path)
        dt = last_valued_date(path)
        if not sha or not dt:
            raise FileNotFoundError("entrada de procedencia ilegible: %s" % name)
        inputs[os.path.basename(name)] = {"sha": sha, "date": dt}
    rec = {
        "schema": SCHEMA,
        "file": os.path.basename(output_file),
        "generated_utc": generated_utc,
        "inputs": inputs,
    }
    dest = path_for_data_dir(data_dir, output_file)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, dest)
    return dest


def last_common_session(obs_by_file):
    if not obs_by_file:
        return None
    sets = list(obs_by_file.values())
    if any(not s for s in sets):
        return None
    common = set.intersection(*sets)
    return max(common) if common else None

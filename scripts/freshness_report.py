#!/usr/bin/env python3
"""freshness_report.py — F3: informe de frescura EN PARALELO (no avisa, no sustituye al motor oficial §05).

    python scripts/freshness_report.py [--at 2026-09-24T12:00:00Z] [--out data/_ingest/freshness.json]
                                       [--compare-dir data/_ingest/freshness_compare] [--git-ref HEAD]

Lee el repositorio (datos, registros de ingestión data/_ingest/latest/*, historial de evidencia
data/_ingest/evidence/*.jsonl) y escribe:
  · freshness.json — vista DERIVADA del historial y de las reglas (se puede regenerar);
  · freshness_compare/<fecha>.json — comparación feed a feed con el motor actual (check_dqm de
    dashboard_alerts.py, importado SIN modificarlo): acuerdos, casos más estrictos y casos más permisivos
    («ampliaciones», que requieren tu decisión antes de que el motor nuevo mande).
Con --git-ref, las fechas de los ficheros y de las entradas de los derivados se leen del historial de git en el
instante --at (reproducción histórica); sin él, del árbol de trabajo.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from g8common import freshness as FR  # noqa: E402
from g8common import runlog, series as S  # noqa: E402

ROOT = os.path.dirname(HERE)
UTC = timezone.utc
SCHEMA = "g8-freshness/1"


def _utc(s):
    return datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


class TreeSource(object):
    """Estado actual del árbol de trabajo (+ historial de git si está disponible para los derivados)."""

    def __init__(self, root):
        self.root = root
        self._git_ok = None

    def read(self, f):
        p = os.path.join(self.root, "data", f)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as fh:
            return fh.read()

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.root] + list(args), capture_output=True, check=True).stdout

    def input_max_at(self, f, instant):
        """Fecha máxima de data/<f> en el instante dado: historial de git si lo hay; si no, historial de evidencia
        (descargas que escribieron); si no, None (INPUT_TIMING_UNKNOWN)."""
        try:
            h = self._git("log", "-1", "--format=%H", "--before=%d" % int(instant.timestamp()), "--", "data/" + f)
            h = h.decode().strip()
            if h:
                ds = FR.obs_dates_from_bytes(self._git("show", "%s:data/%s" % (h, f)))
                return max(ds) if ds else None
        except (OSError, subprocess.CalledProcessError):
            pass
        best = None
        for r in self.evidence(f):
            if r.get("wrote") and r.get("query_utc", "") <= instant.strftime("%Y-%m-%dT%H:%M:%SZ") and r.get("src_max"):
                d = date.fromisoformat(r["src_max"])
                best = d if best is None or d > best else best
        return best

    def evidence(self, f):
        p = os.path.join(self.root, "data", "_ingest", "evidence", f + ".jsonl")
        out = []
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def facts(self):
        """Hechos de ingestión (eje B) por fichero, desde los punteros data/_ingest/latest/*.json."""
        d = os.path.join(self.root, runlog.LATEST_DIR)
        out = {}
        if not os.path.isdir(d):
            return out
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, n), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            files = rec.get("files") or {}
            for f, v in (files.items() if isinstance(files, dict) else []):
                if not isinstance(v, dict):
                    continue
                out[f] = {"pointer": n, "run_id": rec.get("run_id"), "finished_utc": rec.get("finished_utc"),
                          "rc": rec.get("rc"), "status": v.get("status"), "held": len(v.get("held") or []),
                          "detail": (v.get("detail") or "")[:200], "last_ok_utc": rec.get("last_ok_utc"),
                          "failing_since_utc": rec.get("failing_since_utc"),
                          "errors": [e.get("msg", e) if isinstance(e, dict) else e for e in (rec.get("errors") or [])][:5]}
        return out


class GitSource(TreeSource):
    """Reproducción histórica: el contenido de cada fichero es el del último commit (≤ ref) anterior al instante."""

    def __init__(self, root, ref, at):
        TreeSource.__init__(self, root)
        self.ref, self.at = ref, at
        self._log = {}
        self._max = {}

    def _commits(self, f):
        if f not in self._log:
            try:
                raw = self._git("log", self.ref, "--format=%ct %H", "--", "data/" + f).decode().split("\n")
                self._log[f] = [(int(x.split()[0]), x.split()[1]) for x in raw if x.strip()]
            except subprocess.CalledProcessError:
                self._log[f] = []
        return self._log[f]

    def commit_at(self, f, instant):
        ts = instant.timestamp()
        for t, h in self._commits(f):                   # de más reciente a más antiguo
            if t <= ts:
                return h
        return None

    def read_at(self, f, instant):
        h = self.commit_at(f, instant)
        if h is None:
            return None
        try:
            return self._git("show", "%s:data/%s" % (h, f))
        except subprocess.CalledProcessError:
            return None

    def read(self, f):
        return self.read_at(f, self.at)

    def max_at(self, f, instant, date_key=""):
        h = self.commit_at(f, instant)
        if h is None:
            return None
        if (f, h) not in self._max:
            ds = FR.obs_dates_from_bytes(self.read_at(f, instant), date_key)
            self._max[(f, h)] = max(ds) if ds else None
        return self._max[(f, h)]

    def input_max_at(self, f, instant):
        return self.max_at(f, instant)

    def evidence(self, f):
        return []                                        # el historial de evidencia no existía en esas fechas

    def facts(self):
        return {}


def build(root, at, src):
    rules, params, outputs, cals = FR.load_config(root)
    passes = FR.SC.load_passes(root)
    facts = src.facts()
    items = []
    for o in outputs:
        rule, param = rules[o["rule_id"]], params[o["rule_id"]]
        if isinstance(src, GitSource):
            m = src.max_at(o["file"], at, o.get("date_key", ""))
            have = {m} if m else (set() if src.commit_at(o["file"], at) else None)
        else:
            have = FR.obs_dates_from_bytes(src.read(o["file"]), o.get("date_key", ""))
        ev = {f: src.evidence(f) for f in [o["file"]] + [x for x in (o.get("inputs") or "").split(";") if x]}
        r = FR.evaluate(o, rule, param, cals, at, have, input_max_at=src.input_max_at, evidence=ev,
                        facts=facts.get(o["file"], {}), passes=passes)
        if r.get("state") in FR.LATE_STATES or r.get("state") in ("DUE", "PENDING_TIME_UNKNOWN"):
            obs = r.get("missing", [None])[0] if r.get("missing") else r.get("due_obs")
            if obs and ev.get(o["file"]):
                r["availability"] = FR.availability(ev[o["file"]], obs)
        items.append(r)
    counts = {}
    for r in items:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"schema": SCHEMA, "at_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "PARALELO_SIN_AVISOS",
            "official_engine": "dashboard_alerts.py §05 (sin cambios)", "counts": counts, "outputs": items}


# ── comparación con el motor actual (check_dqm, importado sin modificar) ──────────────────────────────────────
def current_engine(root, at, src):
    sys.path.insert(0, os.path.join(root, "scripts"))
    os.environ.setdefault("G8_NO_SEND", "1")
    import dashboard_alerts as DA
    st, lines = {"dqm": {}}, []
    by_path = {}

    def last_csv(path):
        f = os.path.relpath(path, os.path.join(root, "data"))
        if isinstance(src, GitSource):
            m = src.max_at(f, at)
            return m.strftime("%Y%m%d") if m else None
        ds = FR.obs_dates_from_bytes(src.read(f))
        return max(ds).strftime("%Y%m%d") if ds else None

    def read_json(name):
        b = src.read(name)
        try:
            return json.loads(b.decode("utf-8")) if b else None
        except ValueError:
            return None
    with mock.patch.object(DA, "ROOT", root), mock.patch.object(DA, "DATA", os.path.join(root, "data")), \
            mock.patch.object(DA, "REGISTRY", os.path.join(root, "sources", "registry.csv")), \
            mock.patch.object(DA, "TODAY", at.date()), mock.patch.object(DA, "_last_csv_date", last_csv), \
            mock.patch.object(DA, "read_json", read_json), mock.patch.object(DA, "NOTES", []):
        DA.check_dqm(st, lines)
    with open(os.path.join(root, "sources", "registry.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["feed_id"] in st["dqm"]:
                by_path.setdefault(os.path.relpath(r["primary_access"], "data"), []).append(
                    (r["feed_id"], st["dqm"][r["feed_id"]]))
    return by_path


def compare(report, current):
    rows = []
    for r in report["outputs"]:
        cur = current.get(r["file"])
        if not cur:
            continue
        for feed, status in cur:
            new_late = r["state"] in FR.LATE_STATES
            cur_late = status in ("DEGRADED", "STALE", "DEAD")
            if r["state"] == "NOT_MONITORED":
                kind = "FUERA_DE_F3"                     # decisión documentada; el motor oficial lo sigue vigilando
            else:
                kind = "ACUERDO" if new_late == cur_late else ("NUEVO_MAS_ESTRICTO" if new_late else "AMPLIACION_A_DECIDIR")
            rows.append({"feed_id": feed, "file": r["file"], "current": status, "new": r["state"], "kind": kind,
                         "new_basis": r.get("reason") or r.get("basis"), "flags": r.get("flags", [])})
    summary = {}
    for x in rows:
        summary[x["kind"]] = summary.get(x["kind"], 0) + 1
    return {"schema": SCHEMA + "-compare", "at_utc": report["at_utc"], "summary": summary, "rows": rows,
            "note": "AMPLIACION_A_DECIDIR = el motor nuevo no marca atraso donde el actual sí: requiere tu decisión. "
                    "FUERA_DE_F3 = salida no vigilada por F3 por decisión documentada (el motor oficial sigue)"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--at")
    ap.add_argument("--out", default=os.path.join("data", "_ingest", "freshness.json"))
    ap.add_argument("--compare-dir", default=os.path.join("data", "_ingest", "freshness_compare"))
    ap.add_argument("--git-ref")
    a = ap.parse_args(argv)
    at = _utc(a.at) if a.at else datetime.now(UTC).replace(microsecond=0)
    src = GitSource(a.root, a.git_ref, at) if a.git_ref else TreeSource(a.root)
    rep = build(a.root, at, src)
    cmp_ = compare(rep, current_engine(a.root, at, src))
    rep["compare_summary"] = cmp_["summary"]
    out = os.path.join(a.root, a.out) if not os.path.isabs(a.out) else a.out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    S.write_atomic(out, json.dumps(rep, ensure_ascii=False, indent=1, sort_keys=True).encode() + b"\n")
    cdir = os.path.join(a.root, a.compare_dir) if not os.path.isabs(a.compare_dir) else a.compare_dir
    os.makedirs(cdir, exist_ok=True)
    S.write_atomic(os.path.join(cdir, at.strftime("%Y-%m-%dT%H%MZ") + ".json"),
                        json.dumps(cmp_, ensure_ascii=False, indent=1, sort_keys=True).encode() + b"\n")
    print("freshness %s: %s · comparación %s" % (rep["at_utc"], rep["counts"], cmp_["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

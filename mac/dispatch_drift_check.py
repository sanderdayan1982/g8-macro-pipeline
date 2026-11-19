#!/usr/bin/env python3
"""dispatch_drift_check.py — v1.0 (19-nov-2026) · aviso de deriva entre el horario del Mac y el del repo.

Solo lectura y solo aviso: compara los horarios que lee com.g8.dispatch (dispatch_schedule.json y cada
dispatch_schedule_<nombre>.json junto a este script) con sus copias en mac/ de la rama main del repo, y dice qué
turnos están solo en el Mac, solo en el repo, o con distinto workflow, cron o inputs. NO sincroniza ni escribe
ningún horario, no usa token (el repo es público) y no lanza nada.

  · Excepciones declaradas: mac/dispatch_drift_exceptions.json (del repo). Una diferencia declarada ahí no cuenta
    como deriva; una excepción que ya no se da se informa sin cambiar el código de salida.
  · Campos de cabecera (repo, ref, catchup_hours, token_path, …): solo se informa el NOMBRE del campo que
    difiere, nunca su valor. De los turnos se informan id y nombres de campo, no valores.
  · Sin red o con el repo ilegible: no se compara nada (salida 3). No es deriva y no escribe en ALERTAS.log.
  · --alert: además de stdout, una línea en logs/ALERTAS.log cuando hay deriva.
Uso: python3 dispatch_drift_check.py [--root DIR] [--repo-dir DIR] [--alert]
Salida: 0 sin deriva · 1 deriva · 3 no se pudo leer el repo o un horario local.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.join(HERE, "..", "scripts"))   # g8common: junto al script en el Mac, en scripts/ en el repo

REPO = "sanderdayan1982/g8-macro-pipeline"
REF = "main"
API = "https://api.github.com"
EXCEPTIONS = "dispatch_drift_exceptions.json"
MAIN_SCHEDULE = "dispatch_schedule.json"
SLOT_FIELDS = ("workflow", "cron", "inputs")
KINDS = ("solo_repo", "solo_mac", "difiere")


def local_schedules(root):
    names = [MAIN_SCHEDULE]
    for n in sorted(os.listdir(root)):
        if n.startswith("dispatch_schedule_") and n.endswith(".json"):
            names.append(n)
    return names


def read_local(root, name):
    try:
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def repo_reader(repo_dir=None, transport=None, sleep=None):
    """Devuelve read(name) → dict | None (None si no se puede leer)."""
    if repo_dir:
        def read(name):
            return read_local(os.path.join(repo_dir, "mac"), name)
        return read

    from g8common import g8http   # solo al leer de la red: la versión sin red no lo necesita

    def read(name):
        url = "%s/repos/%s/contents/mac/%s?ref=%s" % (API, REPO, name, REF)
        h = {"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "g8-dispatch-drift"}
        res = g8http.fetch(url, provider="github", headers=h, budget=g8http.Budget(60),
                           transport=transport, read_timeout=30, **({"sleep": sleep} if sleep else {}))
        if not res.ok or res.body is None:
            return None
        try:
            body = res.body.decode("utf-8") if isinstance(res.body, bytes) else res.body
            return json.loads(body)
        except ValueError:
            return None
    return read


def compare(local, remote):
    """→ lista de (tipo, id_o_campo, detalle) sin valores."""
    out = []
    for k in sorted((set(local) | set(remote)) - {"slots", "_doc"}):
        if local.get(k) != remote.get(k):
            out.append(("difiere", "campo:" + k, "valor distinto (no se muestra)"))
    ls = {s.get("id"): s for s in local.get("slots") or []}
    rs = {s.get("id"): s for s in remote.get("slots") or []}
    for i in sorted(set(rs) - set(ls)):
        out.append(("solo_repo", i, rs[i].get("workflow", "")))
    for i in sorted(set(ls) - set(rs)):
        out.append(("solo_mac", i, ls[i].get("workflow", "")))
    for i in sorted(set(ls) & set(rs)):
        fields = [f for f in SLOT_FIELDS if (ls[i].get(f) or None) != (rs[i].get(f) or None)]
        if fields:
            out.append(("difiere", i, ",".join(fields)))
    return out


def load_exceptions(read):
    doc = read(EXCEPTIONS) or {}
    out = set()
    for e in doc.get("exceptions") or []:
        if e.get("kind") in KINDS and e.get("file") and e.get("id"):
            out.add((e["file"], e["kind"], e["id"]))
    return out


def main(argv=None, transport=None, now=None, sleep=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=HERE, help="carpeta de los horarios vivos (por defecto, la de este script)")
    ap.add_argument("--repo-dir", help="clon local del repo (sin red); por defecto lee main por la API pública")
    ap.add_argument("--alert", action="store_true", help="escribe la deriva en logs/ALERTAS.log")
    a = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    read = repo_reader(a.repo_dir, transport, sleep)
    exceptions = load_exceptions(read)
    drift, used, rc = [], set(), 0
    for name in local_schedules(a.root):
        local = read_local(a.root, name)
        remote = read(name)
        if local is None or remote is None:
            print("%s · no se pudo leer %s (%s): no se compara" % (
                now.strftime("%Y-%m-%dT%H:%MZ"), name, "local" if local is None else "repo"))
            rc = 3
            continue
        for kind, ident, detail in compare(local, remote):
            key = (name, kind, ident)
            if key in exceptions:
                used.add(key)
                print("excepción declarada: %s %s %s" % (name, kind, ident))
                continue
            drift.append((name, kind, ident, detail))
    for key in sorted(exceptions - used):
        print("excepción sin efecto (ya no se da): %s %s %s" % key)
    for name, kind, ident, detail in drift:
        print("DERIVA %s · %s · %s · %s" % (name, kind, ident, detail))
    if drift:
        if a.alert:
            os.makedirs(os.path.join(a.root, "logs"), exist_ok=True)
            resumen = "; ".join("%s %s %s" % (n, k, i) for n, k, i, _ in drift)
            with open(os.path.join(a.root, "logs", "ALERTAS.log"), "a", encoding="utf-8") as fh:
                fh.write("%s 🟠 Mac horario del lanzador distinto del repo (%d): %s\n" % (
                    now.strftime("%Y-%m-%dT%H:%M:%SZ"), len(drift), resumen))
        return 1
    if rc == 0:
        print("sin deriva: el horario del Mac coincide con el repo (%s)" % REF)
    return rc


if __name__ == "__main__":
    sys.exit(main())

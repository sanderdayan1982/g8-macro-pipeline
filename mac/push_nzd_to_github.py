#!/usr/bin/env python3
"""
push_nzd_to_github.py — v2.0 (lote 1 de fiabilidad de ingestión, 2026-09-24) · ejecutor Mac (launchd)

Qué cambia frente a v1.3 (PUT fichero a fichero con la API de contenidos):
  1. Comprueba el token ANTES de publicar (validez, caducidad, permisos) sin mostrarlo nunca.
  2. Publica por FAMILIAS (NZ-B2, CH-CURVA, CH-DIARIO, JP-TONA): cada familia es un único commit con la
     API de datos Git y actualización de rama fast-forward → todo o nada. Un fallo de una familia no
     bloquea a las demás.
  3. Fusión monótona contra lo PUBLICADO en la rama (no contra la copia local): una descarga vacía,
     truncada o más antigua nunca sustituye lo publicado; se detectan revisiones de valores aunque la
     fecha máxima no cambie; los saltos implausibles y las correcciones históricas quedan en
     confirmación (data/_ingest/quarantine/<familia>.json) y se aceptan con trazabilidad (confirmación
     independiente posterior o decisión manual en data/_ingest/decisions/<familia>/*.json).
  4. Latido: cada ejecución publica su registro (data/_ingest/runs/…) y su puntero (data/_ingest/latest/…),
     aunque no haya datos nuevos. Si el latido no llega, Actions lo detecta (ingest_watch.py).
  5. Avisos locales por Telegram (g8common.notify) independientes de GitHub: token inválido, familia no
     publicada, valores en confirmación, caducidad próxima. G8_NO_SEND=1 → no envía.

Uso:  /usr/bin/python3 push_nzd_to_github.py --fetch-status nzd=0,chf=0,tona=0
      /usr/bin/python3 push_nzd_to_github.py --dry-run          (lee la rama; no escribe nada)
Salida: 0 = todo publicado o sin novedad; 1 = algún fallo (detallado en el log y en el latido).
"""
import argparse
import glob
import importlib.util
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from g8common import g8http, ghpublish as G, series as S, runlog, notify  # noqa: E402

VERSION = "push_nzd_to_github v2.0"
OWNER, REPO, BRANCH = "sanderdayan1982", "g8-macro-pipeline", "main"
JOB = "nzchf-tona"
LOCAL_DATA = os.path.join(HERE, "data")
STATE_DIR = os.path.join(HERE, "state")
LOG_DIR = os.path.join(HERE, "logs")
QUAR = "data/_ingest/quarantine/%s.json"
DECIDE = "data/_ingest/decisions/%s/"
REGISTRY = "sources/registry.csv"

FAMILIES = [
    # id, patrones locales, ficheros obligatorios, cabecera exigida, clave de fetch
    ("NZ-B2", ["NZD_BILL_*.csv", "NZD_BOND_*.csv", "NZD_CASH_ON.csv", "NZD_IIB_*.csv"],   # nunca NZD_OCR (Actions)
     ["NZD_BILL_30D.csv", "NZD_BILL_60D.csv", "NZD_BILL_90D.csv", "NZD_BOND_1Y.csv", "NZD_BOND_2Y.csv",
      "NZD_BOND_5Y.csv", "NZD_BOND_10Y.csv", "NZD_CASH_ON.csv"], ["Date", "Value"], "nzd"),
    ("CH-CURVA", ["CHF_SPOT_*.csv"],
     ["CHF_SPOT_%dY.csv" % n for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 30)], ["Date", "Value"], "chf"),
    ("CH-DIARIO", ["CHF_NOM_10Y.csv", "CHF_SARON.csv"], ["CHF_NOM_10Y.csv", "CHF_SARON.csv"],
     ["Date", "Value", "Source"], "chf"),
    ("JP-TONA", ["TONA.csv"], ["TONA.csv"], ["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], "tona"),
]
# Plausibilidad: SIEMPRE la del registro (valores ya aprobados). Los ficheros sin fila propia heredan
# la de su fila hermana (misma tabla, mismo tipo de instrumento).
SIBLING_ROW = {"NZD_BILL_30D.csv": "NZD_BILL_90D.csv", "NZD_BILL_60D.csv": "NZD_BILL_90D.csv",
               "NZD_BOND_1Y.csv": "NZD_BOND_5Y.csv", "NZD_BOND_2Y.csv": "NZD_BOND_5Y.csv"}
SIBLING_PREFIX = {"NZD_IIB_": "NZD_IIB_2035.csv", "CHF_SPOT_": "CHF_SPOT_10Y.csv"}
# Ventana revisable documentada (obs finales que la fuente reescribe por diseño): fetch_chf_snb.py sustituye
# filas 'rss' por 'curve' (curva mensual) y SARH 'rss' (2 decimales) por 'zirepo' (6 decimales, semanal).
# Resto: SIN ventana documentada → toda revisión pasa por confirmación (provisional hasta medirla).
REVISION_WINDOW = {"CHF_NOM_10Y.csv": 30, "CHF_SARON.csv": 30}


def log(msg):
    print("[push] " + msg, flush=True)


def read_token():
    t = os.environ.get("GITHUB_TOKEN", "").strip()
    if not t:
        p = os.path.expanduser("~/.g8/github_token")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                t = fh.read().strip()
    return t


def executor_id():
    e = os.environ.get("G8_EXECUTOR_ID", "").strip()
    if not e:
        p = os.path.expanduser("~/.g8/executor_id")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                e = fh.read().strip()
    return e or "mac-primary"


def parse_registry(data):
    import csv
    import io
    out = {}
    if not data:
        return out
    for r in csv.DictReader(io.StringIO(data.decode("utf-8"))):
        acc = (r.get("primary_access") or "").strip()
        if acc.startswith("data/"):
            out[acc[5:]] = r
    return out


def plaus_for(fname, reg):
    key = SIBLING_ROW.get(fname)
    if key is None:
        key = next((v for k, v in SIBLING_PREFIX.items() if fname.startswith(k)), fname)
    row = reg.get(key)
    if not row:
        return None

    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    return {"min": f(row.get("plaus_min")), "max": f(row.get("plaus_max")), "max_jump": f(row.get("plaus_max_jump")),
            "from_row": row.get("feed_id")}


def local_files(patterns):
    out = set()
    for p in patterns:
        out.update(os.path.basename(x) for x in glob.glob(os.path.join(LOCAL_DATA, p)))
    return sorted(out)


def make_build(fam, files, header, local, run_id, now_utc, report):
    qpath, dprefix = QUAR % fam, DECIDE % fam
    paths = ["data/" + f for f in files] + [qpath, REGISTRY]

    def fn(cur, head):
        reg = parse_registry(cur.get(REGISTRY))
        qdoc = json.loads(cur[qpath].decode("utf-8")) if cur.get(qpath) else {"family": fam, "candidates": {}}
        decisions = []
        for p, v in sorted(cur.items()):
            if p.startswith(dprefix) and v:
                try:
                    decisions.append(json.loads(v.decode("utf-8")))
                except ValueError:
                    report.setdefault("warnings", []).append("decisión ilegible: " + p)
        q = S.apply_manual_decisions(qdoc.get("candidates", {}), decisions)
        repo_series = {}
        for f in files:
            b = cur.get("data/" + f)
            if b is None:
                repo_series[f] = None
                continue
            try:
                repo_series[f] = S.parse(b)
            except S.SeriesError as e:
                repo_series[f] = None
                report.setdefault("warnings", []).append("%s publicado ilegible (%s): se reconstruye" % (f, e))

        def run(hold):
            return {f: S.merge(f, repo_series[f], local[f], plaus_for(f, reg), REVISION_WINDOW.get(f), q,
                               run_id, now_utc, hold_new_from=hold) for f in files}
        res = run(None)
        bad = {f: r for f, r in res.items() if r.status in (S.INVALID, S.REGRESSION_BLOCKED)}
        if bad:
            report["status"] = "INVALID"
            report["files"] = {f: r.report() for f, r in res.items()}
            report["detail"] = "; ".join("%s: %s %s" % (f, r.status, r.detail) for f, r in sorted(bad.items()))[:600]
            return {}, report
        sus = [r.suspicious_new_from for r in res.values() if r.suspicious_new_from]
        hold = min(sus) if sus else None
        if hold:
            res = run(hold)                                  # coherencia: la familia no avanza más allá de hold
        out = {"data/" + f: r.series.to_bytes() for f, r in res.items() if r.series is not None}
        newq = dict(q)
        for r in res.values():
            for c in r.candidates:
                newq[c["id"]] = c
        if newq != qdoc.get("candidates", {}):
            out[qpath] = runlog.dumps({"family": fam, "candidates": newq, "updated_by_run": run_id})
        held = [dict(h, file=f) for f, r in res.items() for h in r.report()["held"]]
        report["status"] = "HELD" if held else ("PUBLISH" if out else "NOOP")
        report["held"] = held
        report["hold_new_from"] = hold
        report["files"] = {f: r.report() for f, r in res.items()}
        return out, report
    return G.Build(paths, fn, prefixes=(dprefix,))


def env_check():
    return {"python": platform.python_version(),
            "curl_cffi": importlib.util.find_spec("curl_cffi") is not None,
            "openpyxl": importlib.util.find_spec("openpyxl") is not None}


def main(argv=None, repo_factory=None, now=time.time, cfg_dir="~/.g8"):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-status", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    started = datetime.fromtimestamp(now(), tz=timezone.utc)
    ex = executor_id()
    run_id = runlog.new_run_id(ex, JOB, started)
    fetch_status = dict(kv.split("=", 1) for kv in a.fetch_status.split(",") if "=" in kv)
    alerts = {}
    rec = {"run_id": run_id, "executor": ex, "job": JOB, "version": VERSION,
           "started_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "fetch_status": fetch_status,
           "env": env_check(), "families": {}, "credentials": None}
    for k, v in fetch_status.items():
        if v != "0":
            alerts["fetch:" + k] = "Mac: la descarga %s terminó con código %s (ver logs)" % (k, v)
    token = read_token()
    if not token and not a.dry_run:
        alerts["token:missing"] = "Mac: no hay token de GitHub (~/.g8/github_token) — no se puede publicar"
        return finish(rec, alerts, None, a, cfg_dir, now)
    repo = (repo_factory or default_repo)(token if not a.dry_run else (token or None), now)
    if token:
        cred = G.check_token(repo, token, role="data")
        rec["credentials"] = cred
        if not cred["valid"]:
            alerts["token:invalid"] = "Mac: el token de GitHub no es válido (%s). NZD/CHF/TONA no se publican." % cred.get("cls")
            return finish(rec, alerts, None, a, cfg_dir, now)
        if cred.get("days_left") is not None and cred["days_left"] <= 7:
            alerts["token:expiry"] = "Mac: el token de GitHub caduca en %.1f días (%s)" % (cred["days_left"], cred["expires_utc"])
        if cred.get("required_ok") is False:
            alerts["token:scope"] = "Mac: el token no tiene permiso de escritura en repos públicos (public_repo)"
            return finish(rec, alerts, None, a, cfg_dir, now)
    for fam, pats, required, header, fkey in FAMILIES:
        frep = {"status": None, "fetch_rc": fetch_status.get(fkey)}
        rec["families"][fam] = frep
        files = local_files(pats)
        missing = [f for f in required if f not in files]
        if missing:
            frep["status"] = "INVALID"
            frep["detail"] = "faltan en local: " + ", ".join(missing)
            alerts["fam:%s" % fam] = "Mac %s: no publicada — %s" % (fam, frep["detail"])
            continue
        local = {}
        try:
            for f in files:
                with open(os.path.join(LOCAL_DATA, f), "rb") as fh:
                    local[f] = S.parse(fh.read(), expect_header=header)
        except (OSError, S.SeriesError) as e:
            frep["status"] = "INVALID"
            frep["detail"] = "copia local ilegible: %s" % e
            alerts["fam:%s" % fam] = "Mac %s: no publicada — %s" % (fam, frep["detail"])
            continue
        report = {}
        try:
            out = repo.publish(make_build(fam, files, header, local, run_id, started, report),
                               "%s %s %s" % (ex, fam, run_id), dry=a.dry_run)
        except G.GitHubError as e:
            frep.update(status="PUBLISH_FAIL", cls=e.cls, detail=e.detail)
            alerts["fam:%s" % fam] = "Mac %s: descarga correcta pero PUBLICACIÓN FALLIDA (%s)" % (fam, e.cls)
            if e.cls in (g8http.FAIL_AUTH, g8http.FAIL_PERMISSION):
                break
            continue
        frep.update(report)
        frep["outcome"] = out.record()
        if out.status == G.CONFLICT_EXHAUSTED:
            frep["status"] = "PUBLISH_FAIL"
            alerts["fam:%s" % fam] = "Mac %s: la rama cambió en cada intento; no publicada" % fam
        elif frep.get("status") == "INVALID":
            alerts["fam:%s" % fam] = "Mac %s: descarga inválida, se conserva lo publicado — %s" % (fam, frep.get("detail", ""))
        elif frep.get("status") == "HELD":
            alerts["held:%s" % fam] = "Mac %s: %d valor(es) en confirmación (conservado el último válido): %s" % (
                fam, len(frep["held"]), ", ".join("%s %s=%s" % (h["file"], h["date"], h["value"]) for h in frep["held"][:4]))
        if out.status == G.PUBLISHED:
            frep["published"] = True
            if frep.get("status") == "PUBLISH":
                frep["status"] = "PUBLISHED"
        elif out.status == "DRY":
            frep["status"] = "DRY:" + str(frep.get("status"))
    return finish(rec, alerts, repo, a, cfg_dir, now)


def default_repo(token, now):
    return G.Repo(OWNER, REPO, BRANCH, token=token, budget=g8http.Budget(900, now=now), now=now, log=log)


def finish(rec, alerts, repo, a, cfg_dir, now):
    fin = datetime.fromtimestamp(now(), tz=timezone.utc)
    rec["finished_utc"] = fin.strftime("%Y-%m-%dT%H:%M:%SZ")
    rec["alerts"] = sorted(alerts)
    rec["heartbeat"] = {"status": "NOT_ATTEMPTED"}
    if repo is not None and not a.dry_run and rec.get("credentials") and rec["credentials"].get("valid"):
        started = datetime.strptime(rec["started_utc"], "%Y-%m-%dT%H:%M:%SZ")
        rp = runlog.run_path(rec["executor"], JOB, rec["run_id"], started)
        lp = runlog.latest_path(rec["executor"], JOB)
        body = runlog.dumps(rec)
        try:
            out = repo.publish(G.Build([rp, lp], lambda cur, head: ({rp: body, lp: body} if cur.get(rp) is None else {}, None)),
                               "%s heartbeat %s" % (rec["executor"], rec["run_id"]))
            rec["heartbeat"] = out.record()
            if out.status != G.PUBLISHED:
                alerts["heartbeat"] = "Mac: latido no publicado (%s)" % out.status
        except G.GitHubError as e:
            rec["heartbeat"] = {"status": "PUBLISH_FAIL", "cls": e.cls}
            alerts["heartbeat"] = "Mac: latido no publicado (%s) — Actions avisará por ausencia" % e.cls
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    S.write_atomic(os.path.join(STATE_DIR, "last_run.json"), runlog.dumps(rec))
    book = notify.AlertBook(os.path.join(STATE_DIR, "alerts_local.json"))
    now_ts = now()
    plan = book.plan(alerts, now_ts)
    sent, undelivered = set(), []
    pending_before = book.state.get("pending", [])
    msgs = [(k, "🟠 " + t if kind != "RESOLVED" else "🟢 resuelto: " + t) for kind, k, t in plan]
    with open(os.path.join(LOG_DIR, "ALERTAS.log"), "a", encoding="utf-8") as fh:
        for k, t in msgs:
            fh.write("%s %s\n" % (rec["finished_utc"], t))
    if msgs or pending_before:
        text = "<b>G8 · Mac %s</b>\n" % rec["executor"] + "\n".join(t for _, t in msgs)
        if pending_before:
            text += "\n(pendientes anteriores: %d)" % len(pending_before)
        st = notify.send(text, cfg_dir=cfg_dir)
        if st in ("SENT", "DRY"):
            sent = {k for _, k, _ in plan}
        else:
            undelivered = pending_before + [text]
            log("AVISO NO ENTREGADO (%s) — queda en state/alerts_local.json y en logs/ALERTAS.log" % st)
    book.commit(alerts, sent, now_ts, undelivered)
    for fam, fr in rec["families"].items():
        log("%s: %s %s" % (fam, fr.get("status"), fr.get("detail", "")[:160]))
    log("latido: %s · avisos activos: %d" % (rec["heartbeat"].get("status"), len(alerts)))
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())

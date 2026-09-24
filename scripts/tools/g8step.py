#!/usr/bin/env python3
"""g8step — ejecuta un paso del workflow dentro del PLAZO GLOBAL DEL JOB (hallazgo #3 de la revisión).

    python scripts/tools/g8step.py --name sofr --cap 240 -- python scripts/fetch_sofr.py
    python scripts/tools/g8step.py --name s01b --phase post --cap 120 -- python scripts/s01b.py --final
    python scripts/tools/g8step.py --ledger daily           # al final de la fase de descarga: registro + resumen

Plazos (variables que fija el primer paso del workflow):
  G8_JOB_DEADLINE_EPOCH  fin utilizable del job (inicio + timeout-minutes − margen de seguridad)
  G8_JOB_RESERVE_S       tiempo RESERVADO para la fase posterior (S01B, avisos, vigilancia, commit y registro)
Fase «fetch»: el paso termina, como muy tarde, en DEADLINE − RESERVE. Fase «post»: como muy tarde en DEADLINE.
Tiempo concedido = min(--cap, límite de la fase − ahora). Si es menor que --min, el paso NO se ejecuta
(SKIPPED_NO_TIME, código 3). Si se agota, SIGTERM al grupo de procesos, 10 s de gracia y SIGKILL (TIMEOUT, 124).

Al proceso hijo se le pasa su propio plazo (G8_JOB_DEADLINE_EPOCH = ahora + concedido, G8_JOB_RESERVE_S =
gracia): g8http.Budget lo respeta y el descargador termina y escribe (escritura atómica) antes del corte duro.

Guarda de salidas (hallazgo R2-1, P6): antes de lanzar el paso se copia el directorio vigilado (G8_STEP_GUARD,
por defecto ./data; se excluyen de la COPIA los subdirectorios de G8_GUARD_SKIP, por defecto options,futures,
que el Daily no escribe: allí solo se detectan cambios). Al terminar, cada fichero que el paso creó, cambió o
borró se reconcilia:
  · paso CORTADO por tiempo o TERMINADO POR SEÑAL (código negativo, o 128+SIGKILL/SIGTERM/SIGINT/SIGABRT) →
    se restaura TODO lo que cambió (la salida no es fiable: escritura a medias),
    salvo los registros de data/_ingest/ (escritura atómica), que se validan como abajo;
  · paso terminado normalmente (código 0 o ≠0) → se conserva solo lo VÁLIDO según el contrato de su tipo
    (función validate: CSV completo y rectangular con salto final, serie legible sin retroceso, JSON/JSONL
    completos), también para ficheros NUEVOS; un fichero borrado se repone. Lo inválido se restaura.
Así `git add data/` solo ve salidas válidas. Todo lo restaurado (o no restaurable) queda en el registro y en el
aviso de ingest_watch.

Cada paso deja una línea en G8_STEP_LOG (JSONL). --ledger la vuelca a data/_ingest/latest/actions__job_<job>.json
(la lee ingest_watch: los pasos omitidos por tiempo o cortados avisan) y al resumen del job.
Sin G8_JOB_DEADLINE_EPOCH (ejecución local u otro workflow) solo se aplica --cap. Solo stdlib (Python ≥3.9).
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
DEFAULT_SKIP = "options,futures"
GRACE_KILL_S = 10
OK, FAILED, TIMEOUT, SKIPPED = "OK", "FAILED", "TIMEOUT", "SKIPPED_NO_TIME"
ABORTED = "ABORTED"                    # terminación abrupta del hijo (señal): su salida no es fiable (R3-1)
ABRUPT = (TIMEOUT, ABORTED)
ABRUPT_RC = {128 + 9, 128 + 15, 128 + 2, 128 + 6}   # SIGKILL/SIGTERM/SIGINT/SIGABRT vistos a través de un shell


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def phase_limit(phase, env, now):
    dl = env.get("G8_JOB_DEADLINE_EPOCH")
    if not dl:
        return None
    dl = float(dl)
    if phase == "fetch":
        dl -= float(env.get("G8_JOB_RESERVE_S") or 0)
    return dl


def run_step(name, cmd, cap, min_s=20, phase="fetch", env=None, now=time.time, log_path=None, guard=None):
    env = dict(os.environ if env is None else env)
    t0 = now()
    lim = phase_limit(phase, env, t0)
    allowed = float(cap) if lim is None else min(float(cap), lim - t0)
    rec = {"name": name, "phase": phase, "cap_s": cap, "allowed_s": round(allowed, 1), "started_utc": _utc(t0),
           "cmd": " ".join(cmd)[:200]}
    if allowed < min_s:
        rec.update(status=SKIPPED, rc=3, secs=0.0,
                   detail="sin tiempo: quedan %.0f s del plazo de la fase %s (mínimo %s s)" % (allowed, phase, min_s))
        _log(rec, log_path, env)
        print("[g8step] %s: %s — %s" % (name, SKIPPED, rec["detail"]), flush=True)
        return 3
    grace = max(1.0, min(15.0, allowed / 10.0))
    child_env = dict(env)
    child_env["G8_JOB_DEADLINE_EPOCH"] = "%.3f" % (t0 + allowed)
    child_env["G8_JOB_RESERVE_S"] = "%.1f" % grace
    child_env["G8_STEP_NAME"] = name
    guard = guard if guard is not None else env.get("G8_STEP_GUARD", os.path.join(os.getcwd(), "data"))
    g = Guard(guard, env.get("G8_GUARD_SKIP", DEFAULT_SKIP)) if guard and os.path.isdir(guard) else None
    try:
        p = subprocess.Popen(cmd, env=child_env, start_new_session=True)
        try:
            rc = p.wait(timeout=allowed)
            if rc < 0 or rc in ABRUPT_RC:
                sig = -rc if rc < 0 else rc - 128
                try:
                    sname = signal.Signals(sig).name
                except ValueError:
                    sname = "señal %d" % sig
                rec.update(status=ABORTED, rc=rc, detail="terminado por %s: escritura posiblemente incompleta" % sname)
            else:
                rec.update(status=OK if rc == 0 else FAILED, rc=rc)
        except subprocess.TimeoutExpired:
            _kill(p)
            rc = 124
            rec.update(status=TIMEOUT, rc=rc, detail="cortado al agotar %.0f s (fase %s)" % (allowed, phase))
        if g is not None:
            restored, unrestorable, kept = g.reconcile(rec["status"])
            if restored:
                rec["restored"] = restored
            if unrestorable:
                rec["unrestorable"] = unrestorable
            if kept and rec["status"] != OK:
                rec["kept_valid_outputs"] = kept
    finally:
        if g is not None:
            g.close()
    rec["secs"] = round(now() - t0, 1)
    _log(rec, log_path, env)
    print("[g8step] %s: %s rc=%s en %.1f s (concedidos %.0f s)" % (name, rec["status"], rc, rec["secs"], allowed), flush=True)
    return rc


class Guard(object):
    """Copia previa del directorio vigilado y reconciliación de lo que el paso cambió (P6 a nivel de paso)."""

    def __init__(self, root, skip=DEFAULT_SKIP):
        self.root = os.path.abspath(root)
        self.skip = {x.strip() for x in (skip or "").split(",") if x.strip()}
        self.copy = tempfile.mkdtemp(prefix="g8guard_", dir=os.environ.get("RUNNER_TEMP") or None)
        self.before = self._stat()
        for rel in self.before:
            if not self._skipped(rel):
                dst = os.path.join(self.copy, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(self.root, rel), dst)

    def _skipped(self, rel):
        return rel.split(os.sep, 1)[0] in self.skip

    def _stat(self):
        out = {}
        for dp, _, names in os.walk(self.root):
            for n in names:
                p = os.path.join(dp, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out[os.path.relpath(p, self.root)] = (st.st_size, st.st_mtime_ns)
        return out

    def reconcile(self, status):
        after = self._stat()
        restored, unrestorable, kept = [], [], []
        for rel in sorted(set(self.before) | set(after)):
            if self.before.get(rel) == after.get(rel):
                continue
            new = os.path.join(self.root, rel)
            old = os.path.join(self.copy, rel) if rel in self.before else None
            if status in ABRUPT and not rel.startswith("_ingest" + os.sep):
                # los registros de _ingest/ se escriben de forma atómica: se validan en vez de descartarse,
                # para no perder el rastro (ni un Retry-After) del paso cortado
                why = ("paso cortado por tiempo" if status == TIMEOUT else "paso terminado por señal") + \
                    ": salida no fiable"
            elif rel not in after:
                why = "borrado por el paso"
            else:
                why = validate(old, new)
            if not why:
                kept.append(rel)
                continue
            if self._skipped(rel):
                unrestorable.append({"file": rel, "reason": why + " (directorio sin copia previa)"})
                continue
            try:
                if old is not None:
                    tmp = new + ".g8restore.tmp"
                    os.makedirs(os.path.dirname(new), exist_ok=True)
                    shutil.copy2(old, tmp)
                    os.replace(tmp, new)
                elif os.path.exists(new):
                    os.remove(new)
                restored.append({"file": rel, "reason": why, "action": "restaurado" if old else "retirado (nuevo)"})
            except OSError as e:
                unrestorable.append({"file": rel, "reason": "%s; restauración fallida: %s" % (why, e)})
        return restored, unrestorable, kept

    def close(self):
        shutil.rmtree(self.copy, ignore_errors=True)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _csv_contract(data):
    """Contrato GENÉRICO de un CSV (vale también para los ficheros auxiliares): texto UTF-8, no HTML/XML,
    cabecera, TODAS las filas con el mismo número de campos que la cabecera y terminado en salto de línea
    (evidencia de que el escritor cerró la última fila). → None o el motivo."""
    import csv
    import io
    try:
        txt = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "no es texto UTF-8"
    head = txt.lstrip()[:1]
    if head == "<":
        return "contenido HTML/XML en lugar de CSV"
    try:
        rows = [r for r in csv.reader(io.StringIO(txt)) if r and not (len(r) == 1 and not r[0].strip())]
    except csv.Error as e:
        return "CSV mal formado: %s" % e
    body = [r for r in rows if not r[0].startswith("#")]
    if not body:
        return "sin cabecera"
    k = len(body[0])
    for i, r in enumerate(body[1:], 2):
        if len(r) != k:
            return "fila %d con %d campos (la cabecera tiene %d): fila incompleta" % (i, len(r), k)
    if not txt.endswith("\n"):
        return "última fila sin terminar (falta el salto de línea final)"
    return None


def _looks_series(data):
    """¿Cabecera de serie (fecha en la 1.ª columna + columna de valor conocida)?"""
    from g8common import series as S
    first = data.decode("utf-8-sig", "replace").lstrip().split("\n", 1)[0]
    cols = [c.strip().strip('"') for c in first.split(",")]
    return bool(cols) and cols[0].lower() in ("date", "fecha", "time_period") and any(c in S.VALUE_COLS for c in cols)


def validate(old, new):
    """None si `new` es una salida aceptable; si no, el motivo. `old` = ruta de la copia previa o None (nuevo).
    Los ficheros NUEVOS se validan con el contrato de su tipo aunque no haya versión anterior (R3-1):
      · .csv  → contrato genérico (_csv_contract); si es una serie (lo era antes o su cabecera lo indica),
                además serie legible (fechas reales, valores numéricos, sin duplicados) y fecha máxima que
                no retrocede;
      · .json → JSON completo; .jsonl → cada línea JSON completa y salto final;
      · otros → no vacío.
    Un fichero anterior que ya incumplía el contrato genérico (formato auxiliar propio) no se le exige más de
    lo que cumplía: solo que no quede vacío, se mantiene su compatibilidad."""
    try:
        data = _read(new)
    except OSError as e:
        return "ilegible: %s" % e
    if not data:
        return "fichero vacío"
    olddata = None
    if old:
        try:
            olddata = _read(old)
        except OSError:
            olddata = None
    low = new.lower()
    if low.endswith(".csv"):
        from g8common import series as S
        legacy_ok = olddata is not None and _csv_contract(olddata) is not None     # el anterior ya no lo cumplía
        why = _csv_contract(data)
        if why and not (legacy_ok and "salto de línea" in why and _csv_contract(data + b"\n") is None):
            return why
        o = None
        if olddata is not None:
            try:
                o = S.parse(olddata)
            except S.SeriesError:
                o = None
        if o is not None or _looks_series(data):
            try:
                n = S.parse(data)
            except S.SeriesError as e:
                return "serie ilegible: %s" % e
            if o is not None and n.max_date < o.max_date:
                return "la fecha máxima retrocede (%s → %s)" % (o.max_date, n.max_date)
    elif low.endswith(".jsonl"):
        if not data.endswith(b"\n"):
            return "última línea JSONL sin terminar"
        for i, ln in enumerate(data.splitlines(), 1):
            if ln.strip():
                try:
                    json.loads(ln.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as e:
                    return "línea %d JSONL ilegible: %s" % (i, str(e)[:60])
    elif low.endswith(".json"):
        try:
            json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            return "JSON ilegible: %s" % str(e)[:80]
    return None


def _kill(p):
    for sig, wait in ((signal.SIGTERM, GRACE_KILL_S), (signal.SIGKILL, 5)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _log(rec, log_path, env):
    path = log_path or env.get("G8_STEP_LOG")
    if not path:
        return
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")


def ledger(job, env=None, root=ROOT, now=time.time):
    """Vuelca el registro de pasos a data/_ingest/latest/actions__job_<job>.json y al resumen del job."""
    env = os.environ if env is None else env
    path = env.get("G8_STEP_LOG")
    steps = []
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            steps = [json.loads(x) for x in fh if x.strip()]
    bad = [s for s in steps if s["status"] in (TIMEOUT, SKIPPED)]
    restored = [dict(r, step=s["name"]) for s in steps for r in s.get("restored", [])]
    unrestorable = [dict(r, step=s["name"]) for s in steps for r in s.get("unrestorable", [])]
    failed = [{"name": s["name"], "rc": s.get("rc"), "status": s["status"]} for s in steps if s["status"] in (FAILED, ABORTED)]
    doc = {"job": job, "executor": "actions", "run_id": env.get("GITHUB_RUN_ID"), "written_utc": _utc(now()),
           "deadline_utc": _utc(float(env["G8_JOB_DEADLINE_EPOCH"])) if env.get("G8_JOB_DEADLINE_EPOCH") else None,
           "reserve_s": env.get("G8_JOB_RESERVE_S"), "steps": steps,
           "time_limited": [{"name": s["name"], "status": s["status"], "detail": s.get("detail", "")} for s in bad],
           "failed": failed, "restored": restored, "unrestorable": unrestorable}
    out = os.path.join(root, "data", "_ingest", "latest", "actions__job_%s.json" % job)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, out)
    summ = env.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as fh:
            fh.write("\n### Plazo del job (g8step)\n\n| Paso | Estado | s | concedidos |\n|---|---|---|---|\n")
            for s in steps:
                fh.write("| %s | %s | %s | %s |\n" % (s["name"], s["status"], s.get("secs"), s.get("allowed_s")))
    print("[g8step] registro de %d pasos → %s (%d limitados por tiempo)" % (len(steps), out, len(bad)))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = []
    if "--" in argv:
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--cap", type=float, default=300)
    ap.add_argument("--min", type=float, default=20)
    ap.add_argument("--phase", choices=("fetch", "post"), default="fetch")
    ap.add_argument("--ledger")
    a = ap.parse_args(argv)
    if a.ledger:
        return ledger(a.ledger)
    if not a.name or not cmd:
        ap.error("--name y un comando tras -- son obligatorios")
    return run_step(a.name, cmd, a.cap, a.min, a.phase)


if __name__ == "__main__":
    sys.exit(main())

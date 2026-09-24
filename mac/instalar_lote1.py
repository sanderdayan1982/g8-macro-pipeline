#!/usr/bin/env python3
"""instalar_lote1.py — v2.0 (revisión 24-sep, hallazgo #2) · instala o revierte el lote 1 del Mac como UN CONJUNTO.

Lo llaman instalar_lote1.command y revertir_lote1.command (carpeta ~/Trading_Sander/g8-nzd). Solo stdlib, Python ≥3.9.
No guarda credenciales, no muestra el token y no publica nada: la validación usa la simulación (--dry-run).

Instalación (cada fase aborta sin tocar lo que está en uso si algo falla):
  A. Paquete: comprueba lote1/MANIFEST.sha256 (ficheros completos y sin alterar).
  B. Preparación en una carpeta aparte (.g8_staging_<ts>): copia del código nuevo + plist generado para ESTA carpeta.
  C. Validación en esa carpeta: compilación con el Python del Mac, simulación del publicador contra la rama
     (sin escribir), comprobación del token y del plist. Bloquea: token ausente/inválido/sin permiso, familia
     INVALID o PUBLISH_FAIL, error inesperado. No bloquea: valores en confirmación (se muestran).
  D. Confirmación («s»). Con «N» se borra la preparación y NADA cambia.
  E. Activación: fuera de ±10 min de las 08:00/17:00; espera a que no haya ninguna ejecución en curso; toma el
     cerrojo state/run.lock (nzd_local_run.sh v1.4 lo respeta); descarga el trabajo de launchd; copia de
     seguridad COMPLETA (código + plist + estado previo de cada fichero); sustitución por renombrado; verificación
     en su sitio; si falla → restauración automática del conjunto anterior y recarga del plist anterior.
     Solo al final se libera el cerrojo y se carga el plist nuevo.
Reversión (--revert [carpeta_de_copia]): mismo protocolo (ventana, espera, cerrojo, launchd descargado) y
restaura exactamente el conjunto guardado (incluido borrar lo que antes no existía) y su plist.
"""
import argparse
import hashlib
import json
import os
import plistlib
import py_compile
import shutil
import subprocess
import sys
import time
from datetime import datetime

LABEL = "com.g8.nzd-b2"
FILES = ["push_nzd_to_github.py", "check_credentials.py", "nzd_local_run.sh", "com.g8.nzd-b2.plist"]
DIRS = ["g8common"]
SCHEDULE = [(8, 0), (17, 0)]
WINDOW_MIN = 10
BUSY_PATTERNS = ["nzd_local_run.sh", "push_nzd_to_github.py", "fetch_nzd_b2.py", "fetch_chf_snb.py", "fetch_tona_mac.py"]
BLOCKING_ALERTS = ("token:missing", "token:invalid", "token:scope")


class Abort(Exception):
    pass


class Env(object):
    """Todo lo que toca el sistema, sustituible en pruebas."""

    def __init__(self, root, python=sys.executable, agents=None, launchctl=None, now=time.time, sleep=time.sleep,
                 busy=None, runner=None, ask=None, out=print):
        self.root = os.path.abspath(root)
        self.python = python
        self.agents = agents or os.path.expanduser("~/Library/LaunchAgents")
        self.launchctl = launchctl or self._launchctl
        self.now, self.sleep = now, sleep
        self.busy = busy or self._busy
        self.runner = runner or self._run
        self.ask = ask or (lambda q: input(q).strip().lower())
        self.out = out

    @property
    def agent(self):
        return os.path.join(self.agents, LABEL + ".plist")

    @staticmethod
    def _launchctl(*args):
        return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True).returncode

    @staticmethod
    def _busy():
        found = []
        for p in BUSY_PATTERNS:
            r = subprocess.run(["pgrep", "-f", p], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                found.append(p)
        return found

    def _run(self, cmd, cwd, timeout=600):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")


# ── utilidades ───────────────────────────────────────────────────────────────
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def verify_manifest(pkg):
    mf = os.path.join(pkg, "MANIFEST.sha256")
    if not os.path.exists(mf):
        raise Abort("falta %s" % mf)
    bad = []
    with open(mf, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            digest, rel = line.strip().split("  ", 1)
            p = os.path.join(pkg, rel)
            if not os.path.exists(p) or sha256(p) != digest:
                bad.append(rel)
    if bad:
        raise Abort("paquete incompleto o alterado: %s" % ", ".join(bad[:10]))


def render_plist(template_path, root):
    """plist del paquete con las rutas de ESTA carpeta (no se confía en una ruta escrita a mano)."""
    with open(template_path, "rb") as fh:
        pl = plistlib.load(fh)
    if pl.get("Label") != LABEL:
        raise Abort("plist con Label inesperado: %r" % pl.get("Label"))
    hours = sorted((d.get("Hour"), d.get("Minute")) for d in pl.get("StartCalendarInterval", []))
    if hours != sorted(SCHEDULE):
        raise Abort("plist con horario inesperado %s (esperado %s)" % (hours, SCHEDULE))
    pl["ProgramArguments"] = ["/bin/zsh", os.path.join(root, "nzd_local_run.sh")]
    pl["StandardOutPath"] = os.path.join(root, "logs", "launchd.out")
    pl["StandardErrorPath"] = os.path.join(root, "logs", "launchd.err")
    return plistlib.dumps(pl)


def compile_set(env, folder):
    """Compila con el Python del Mac solo los ficheros del conjunto (FILES .py + DIRS) que haya en folder."""
    paths = [os.path.join(folder, f) for f in FILES if f.endswith(".py") and os.path.exists(os.path.join(folder, f))]
    for d in DIRS:
        for dirpath, _, names in os.walk(os.path.join(folder, d)):
            paths += [os.path.join(dirpath, n) for n in names if n.endswith(".py")]
    for p in paths:
        rc, out = env.runner([env.python, "-c", "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)", p], folder, 120)
        if rc != 0:
            raise Abort("no compila con %s: %s\n%s" % (env.python, p, out[-400:]))


def dry_run(env, folder):
    """Simulación del publicador en `folder`. → dict del informe (Abort si hay bloqueo)."""
    rc, out = env.runner([env.python, "push_nzd_to_github.py", "--dry-run", "--fetch-status", "nzd=0,chf=0,tona=0"], folder, 600)
    rep_path = os.path.join(folder, "state", "last_dry_run.json")
    if rc not in (0, 1) or not os.path.exists(rep_path):
        raise Abort("la simulación terminó con código %s:\n%s" % (rc, out[-800:]))
    with open(rep_path, encoding="utf-8") as fh:
        rep = json.load(fh)
    block = [a for a in rep.get("alerts", []) if a in BLOCKING_ALERTS]
    fams = rep.get("families", {})
    for fam, fr in sorted(fams.items()):
        st = str(fr.get("status", "")).replace("DRY:", "")
        if st in ("INVALID", "PUBLISH_FAIL", "None"):
            block.append("%s=%s %s" % (fam, st, (fr.get("detail") or "")[:160]))
    if not fams and not block:
        block.append("la simulación no evaluó ninguna familia")
    if block:
        raise Abort("la simulación bloquea la instalación: %s" % "; ".join(block))
    return rep


def check_token(env, folder):
    rc, out = env.runner([env.python, "check_credentials.py"], folder, 120)
    if rc == 1 or rc not in (0, 2):
        raise Abort("comprobación del token fallida (código %s): %s" % (rc, out[-300:]))
    if rc == 2:
        env.out("AVISO: el token caduca en ≤7 días. Renuévalo pronto (la instalación continúa).")


def in_window(env):
    t = datetime.fromtimestamp(env.now())
    m = t.hour * 60 + t.minute
    for h, mi in SCHEDULE:
        if abs(m - (h * 60 + mi)) <= WINDOW_MIN:
            return "%02d:%02d" % (h, mi)
    return None


def wait_idle(env, max_wait_s=600):
    t0 = env.now()
    while True:
        b = env.busy()
        if not b:
            return
        if env.now() - t0 >= max_wait_s:
            raise Abort("hay una ejecución en curso desde hace más de %d min (%s); inténtalo más tarde" % (max_wait_s // 60, ", ".join(b)))
        env.out("esperando a que termine la ejecución en curso (%s)…" % ", ".join(b))
        env.sleep(15)


def take_lock(env):
    lock = os.path.join(env.root, "state", "run.lock")
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    try:
        os.mkdir(lock)
    except FileExistsError:
        raise Abort("cerrojo %s ocupado (ejecución o instalación en curso)" % lock)
    return lock


def release_lock(lock):
    try:
        os.rmdir(lock)
    except OSError:
        pass


def snapshot(env, dest):
    """Copia COMPLETA del conjunto activo (y qué no existía) en dest."""
    os.makedirs(dest)
    state = {}
    for f in FILES:
        p = os.path.join(env.root, f)
        state[f] = os.path.exists(p)
        if state[f]:
            shutil.copy2(p, os.path.join(dest, f))
    for d in DIRS:
        p = os.path.join(env.root, d)
        state[d + "/"] = os.path.isdir(p)
        if state[d + "/"]:
            shutil.copytree(p, os.path.join(dest, d), ignore=shutil.ignore_patterns("__pycache__"))
    state["agent"] = os.path.exists(env.agent)
    if state["agent"]:
        shutil.copy2(env.agent, os.path.join(dest, "agent.plist"))
    with open(os.path.join(dest, "SET.json"), "w", encoding="utf-8") as fh:
        json.dump({"existed": state, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(env.now()))}, fh, indent=1)
    return state


def place(env, src_dir, existed=None):
    """Sustituye el conjunto por el de src_dir: ficheros por renombrado atómico; carpeta por intercambio."""
    tag = str(int(env.now() * 1000))
    for f in FILES:
        src, dst = os.path.join(src_dir, f), os.path.join(env.root, f)
        if os.path.exists(src):
            tmp = os.path.join(env.root, ".%s.new_%s" % (f, tag))
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        elif existed is not None and not existed.get(f) and os.path.exists(dst):
            os.replace(dst, os.path.join(src_dir, "%s.retirado_%s" % (f, tag)))     # no existía antes: se retira
    for d in DIRS:
        src, dst = os.path.join(src_dir, d), os.path.join(env.root, d)
        old = os.path.join(env.root, ".%s.old_%s" % (d, tag))
        if os.path.isdir(src):
            tmp = os.path.join(env.root, ".%s.new_%s" % (d, tag))
            shutil.copytree(src, tmp)
            if os.path.isdir(dst):
                os.replace(dst, old)
            os.replace(tmp, dst)
            shutil.rmtree(old, ignore_errors=True)
        elif existed is not None and not existed.get(d + "/") and os.path.isdir(dst):
            os.replace(dst, os.path.join(src_dir, "%s.retirado_%s" % (d, tag)))
    agent_src = os.path.join(src_dir, "agent.plist")
    if os.path.exists(agent_src):
        os.makedirs(env.agents, exist_ok=True)
        tmp = env.agent + ".new_" + tag
        shutil.copy2(agent_src, tmp)
        os.replace(tmp, env.agent)
    elif existed is not None and not existed.get("agent") and os.path.exists(env.agent):
        os.replace(env.agent, os.path.join(src_dir, "agent.retirado_%s" % tag))


class RecoveryFailed(Abort):
    """La restauración automática no pudo verificarse: requiere intervención manual."""


def set_matches(env, ref_dir, existed):
    """¿El conjunto activo (código + plist de launchd) coincide byte a byte con el guardado en ref_dir?"""
    bad = []
    for f in FILES:
        p, r = os.path.join(env.root, f), os.path.join(ref_dir, f)
        if existed.get(f):
            if not os.path.exists(p) or sha256(p) != sha256(r):
                bad.append(f)
        elif os.path.exists(p):
            bad.append(f + " (no existía)")
    for d in DIRS:
        p, r = os.path.join(env.root, d), os.path.join(ref_dir, d)
        if existed.get(d + "/"):
            def files(x):
                return {os.path.relpath(os.path.join(dp, n), x): sha256(os.path.join(dp, n))
                        for dp, _, ns in os.walk(x) if "__pycache__" not in dp for n in ns if not n.endswith(".pyc")}
            if not os.path.isdir(p) or files(p) != files(r):
                bad.append(d + "/")
        elif os.path.isdir(p):
            bad.append(d + "/ (no existía)")
    if existed.get("agent"):
        if not os.path.exists(env.agent) or sha256(env.agent) != sha256(os.path.join(ref_dir, "agent.plist")):
            bad.append("plist de launchd")
    elif os.path.exists(env.agent):
        bad.append("plist de launchd (no existía)")
    return bad


def _loaded(env):
    return env.launchctl("list", LABEL) == 0


def _restore_previous(env, backup, existed, loaded_before, why):
    """Restaura el conjunto y el estado de programación previos y lo VERIFICA. Siempre lanza una excepción:
    Abort si la recuperación se verificó; RecoveryFailed si no (con instrucciones)."""
    problems = []
    try:
        if _loaded(env):
            env.launchctl("unload", env.agent)
        place(env, backup, existed)
        problems += ["difiere: " + x for x in set_matches(env, backup, existed)]
        if not problems:
            compile_set(env, env.root)
        if loaded_before:
            if env.launchctl("load", env.agent) != 0 or not _loaded(env):
                problems.append("no se pudo volver a cargar la programación anterior en launchd")
        elif _loaded(env):
            problems.append("launchd quedó cargado y antes no lo estaba")
    except Exception as e:                                          # noqa: BLE001
        problems.append("%s: %s" % (type(e).__name__, e))
    if problems:
        raise RecoveryFailed(
            "RECUPERACIÓN FALLIDA tras «%s»: %s. El conjunto previo completo está en %s. Restáuralo a mano "
            "(copiar sus ficheros a la carpeta y agent.plist a %s; luego «launchctl load» de ese plist) o "
            "ejecuta ./revertir_lote1.command %s" % (why, "; ".join(problems), backup, env.agent, backup))
    raise Abort("%s — se restauró y verificó el conjunto anterior y su programación (launchd %s)" % (
        why, "cargado" if loaded_before else "sin cargar, como estaba"))


def transaction(env, force_window, backup, apply_fn, want_loaded=True):
    """Cambio del conjunto como UNA operación (hallazgo R2-2):
      1. fuera de la ventana horaria, sin ejecuciones en curso, cerrojo state/run.lock;
      2. launchd descargado (comprobado); copia completa del conjunto en `backup`;
      3. apply_fn() (sustituye y verifica en su sitio);
      4. se libera el cerrojo y se carga el plist nuevo; la carga se COMPRUEBA con «launchctl list»;
    cualquier fallo en 2–4 restaura el conjunto y el estado de programación previos y verifica la restauración.
    Devuelve el dict `existed` de la copia."""
    w = in_window(env)
    if w and not force_window:
        raise Abort("demasiado cerca de la ejecución programada de las %s (±%d min): inténtalo más tarde" % (w, WINDOW_MIN))
    wait_idle(env)
    lock = take_lock(env)
    existed = None
    loaded_before = _loaded(env)
    try:
        if loaded_before and (env.launchctl("unload", env.agent) != 0 or _loaded(env)):
            if not _loaded(env):
                env.launchctl("load", env.agent)
            raise Abort("no se pudo descargar la tarea de launchd: no se ha cambiado nada")
        try:
            wait_idle(env, 120)                    # carrera: una ejecución que empezó justo antes de descargar
        except Abort:
            if loaded_before and env.launchctl("load", env.agent) != 0:
                raise RecoveryFailed("ejecución en curso y la programación anterior no se pudo volver a cargar: "
                                     "carga %s a mano con launchctl" % env.agent)
            raise
        try:
            existed = snapshot(env, backup)
        except Exception as e:                                      # noqa: BLE001
            shutil.rmtree(backup, ignore_errors=True)
            if loaded_before and env.launchctl("load", env.agent) != 0:
                raise RecoveryFailed("la copia de seguridad falló (%s) y la programación anterior no se pudo recargar" % e)
            raise Abort("no se pudo crear la copia de seguridad (%s): no se ha cambiado nada" % e)
        try:
            apply_fn()
        except Exception as e:                                      # noqa: BLE001
            env.out("¡Fallo tras la sustitución (%s)! Restaurando el conjunto anterior…" % e)
            _restore_previous(env, backup, existed, loaded_before, "fallo tras la sustitución: %s" % e)
    finally:
        release_lock(lock)
    if not want_loaded:
        return existed
    rc = env.launchctl("load", env.agent)                           # RunAtLoad arranca aquí la primera ejecución
    if rc != 0 or not _loaded(env):
        env.out("¡launchctl load devolvió %s! Restaurando el conjunto y la programación anteriores…" % rc)
        wait_idle(env, 300)
        lock = take_lock(env)
        try:
            _restore_previous(env, backup, existed, loaded_before, "activación de launchd fallida (código %s)" % rc)
        finally:
            release_lock(lock)
    return existed


# ── instalación ──────────────────────────────────────────────────────────────
def install(env, pkg, yes=False, force_window=False):
    ts = datetime.fromtimestamp(env.now()).strftime("%Y%m%d%H%M%S")
    env.out("A. paquete: %s" % pkg)
    verify_manifest(pkg)
    stage = os.path.join(env.root, ".g8_staging_%s" % ts)
    env.out("B. preparación en %s" % stage)
    os.makedirs(os.path.join(stage, "state"))
    try:
        for f in FILES:
            if f != "com.g8.nzd-b2.plist":
                shutil.copy2(os.path.join(pkg, f), os.path.join(stage, f))
        for d in DIRS:
            shutil.copytree(os.path.join(pkg, d), os.path.join(stage, d), ignore=shutil.ignore_patterns("__pycache__"))
        plist = render_plist(os.path.join(pkg, "com.g8.nzd-b2.plist"), env.root)
        for name in ("com.g8.nzd-b2.plist", "agent.plist"):
            with open(os.path.join(stage, name), "wb") as fh:
                fh.write(plist)
        os.symlink(os.path.join(env.root, "data"), os.path.join(stage, "data"))
        os.makedirs(os.path.join(stage, "logs"))
        env.out("C. validación en la carpeta de preparación (nada activo cambia)")
        compile_set(env, stage)
        rep = dry_run(env, stage)
        check_token(env, stage)
        held = [(f, fr.get("held")) for f, fr in rep.get("families", {}).items() if fr.get("held")]
        for f, h in held:
            env.out("   información: %s tiene %d valor(es) que quedarían en confirmación" % (f, len(h)))
        env.out("   simulación correcta: %s" % ", ".join("%s=%s" % (f, fr.get("status")) for f, fr in sorted(rep["families"].items())))
    except Abort:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if not yes and env.ask("D. ¿Instalar y activar el lote 1 (08:00 + 17:00)? [s/N] ") != "s":
        shutil.rmtree(stage, ignore_errors=True)
        env.out("Cancelado: no se ha cambiado nada.")
        return "CANCELLED"
    backup = os.path.join(env.root, "backup_lote1_%s" % ts)

    def apply_new():
        env.out("E. activación (launchd descargado, cerrojo tomado); copia completa en %s" % backup)
        place(env, stage)
        bad = set_matches(env, stage, {k: True for k in FILES + [d + "/" for d in DIRS] + ["agent"]})
        if bad:
            raise Abort("verificación tras la sustitución: %s no coincide" % ", ".join(bad))
        compile_set(env, env.root)
        dry_run(env, env.root)

    try:
        transaction(env, force_window, backup, apply_new)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    with open(os.path.join(env.root, "state", "install_lote1.json"), "w", encoding="utf-8") as fh:
        json.dump({"installed_local": ts, "backup": backup, "launchd": "cargado y comprobado"}, fh, indent=1)
    env.out("Instalado. launchd cargado y comprobado. Reversión: ./revertir_lote1.command")
    return "INSTALLED"


def revert(env, backup=None, yes=False, force_window=False):
    if backup is None:
        cands = sorted(d for d in os.listdir(env.root) if d.startswith("backup_lote1_"))
        if not cands:
            raise Abort("no hay copias backup_lote1_* en %s" % env.root)
        backup = os.path.join(env.root, cands[-1])
    with open(os.path.join(backup, "SET.json"), encoding="utf-8") as fh:
        target = json.load(fh)["existed"]
    if not yes and env.ask("¿Restaurar el conjunto guardado en %s? [s/N] " % backup) != "s":
        env.out("Cancelado: no se ha cambiado nada.")
        return "CANCELLED"
    ts = datetime.fromtimestamp(env.now()).strftime("%Y%m%d%H%M%S")
    pre = os.path.join(env.root, "prerevert_lote1_%s" % ts)        # copia del conjunto actual (por si falla)

    def apply_old():
        place(env, backup, target)
        bad = set_matches(env, backup, target)
        if bad:
            raise Abort("verificación tras restaurar: %s" % ", ".join(bad))
        compile_set(env, env.root)

    transaction(env, force_window, pre, apply_old, want_loaded=bool(target.get("agent")))
    env.out("Revertido al conjunto de %s (launchd %s)." % (backup, "cargado y comprobado" if target.get("agent") else "sin plist"))
    return "REVERTED"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", nargs="?", const="", default=None)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--force-window", action="store_true")
    a = ap.parse_args(argv)
    pkg = os.path.dirname(os.path.abspath(__file__))
    env = Env(os.path.dirname(pkg))
    try:
        if a.revert is not None:
            revert(env, a.revert or None, a.yes, a.force_window)
        else:
            install(env, pkg, a.yes, a.force_window)
    except Abort as e:
        print("ABORTADO: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

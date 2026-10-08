#!/usr/bin/env python3
"""dispatch_workflows.py — v1.0 (acta P-12, 6-oct-2026) · lanza a su hora los workflows de datos de GitHub Actions.

GitHub ejecuta los cron de Actions con 4–8 h de retraso (medido 5/6-oct: el de las 13:17Z empezó a las 18:54–21:05Z),
y eso dejaba el Daily pasada la medianoche, el §01-b sin cerrar y el Factor USD de la tarde por la noche.
Este job del Mac (launchd cada 5 min) pide cada workflow con workflow_dispatch en su hora nominal (UTC, la misma del
cron). El cron de GitHub se queda como segundo intento: llega horas después y es inocuo (fetch idempotente, cierre
§01-b de una sola escritura). Solo stdlib + g8common (Python 3.9 del Mac).

  · Turnos: dispatch_schedule.json (mismas expresiones cron; tests/test_p12_mac_dispatch.py vigila que no se desvíen).
    Ventana de recuperación: catchup_hours tras la hora nominal (Mac dormido); fuera de ella manda el cron de GitHub.
  · Una vez por turno: state/dispatch_state.json guarda el último turno lanzado de cada entrada y SOLO se escribe tras
    la respuesta 2xx de GitHub; un fallo se reintenta en la pasada siguiente dentro de la ventana.
  · Credencial: ~/.g8/github_dispatch_token — token fine-grained de ESTE repo con «Actions: Read and write» y nada más
    (no es el token de datos). Nunca se imprime. Sin token, inválido o caduca en ≤ 7 días → logs/ALERTAS.log.
  · Otros repos (acta NETLIFY_CREDITS, 8-oct-2026): cada dispatch_schedule_<nombre>.json junto a este script es un horario
    más, con su "repo" y su "token_path" (token propio de ese repo). Los id de turno son únicos entre horarios (mismo
    state). Sin token de un horario → se avisa y se salta solo ese horario.
Uso: python3 dispatch_workflows.py [--dry-run] [--check] [--now 2026-10-06T13:20:00Z]
Salida: 0 bien · 1 sin token / token inválido / algún lanzamiento fallido · 2 (--check) el token caduca en ≤ 7 días.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from g8common import g8http, ghpublish  # noqa: E402

TOKEN_PATH = "~/.g8/github_dispatch_token"
API = "https://api.github.com"
EXPIRY_WARN_DAYS = 7


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# ── cron (solo lo que usan los workflows: números, *, a-b y listas; día de semana 0/7 = domingo) ─────────────
def _field(expr, lo, hi):
    out = set()
    for part in expr.split(","):
        if part == "*":
            out.update(range(lo, hi + 1))
        elif "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    bad = [v for v in out if not lo <= v <= hi]
    if bad:
        raise ValueError("valor fuera de rango en %r: %s" % (expr, bad))
    return out


def parse_cron(expr):
    f = expr.split()
    if len(f) != 5 or f[2] != "*" or f[3] != "*":
        raise ValueError("cron no soportado (solo minuto hora * * díasemana): %r" % expr)
    dows = {d % 7 for d in _field(f[4], 0, 7)}
    return _field(f[0], 0, 59), _field(f[1], 0, 23), dows


def last_occurrence(expr, now):
    """Último instante (UTC) ≤ now que encaja con el cron, buscando hasta 8 días atrás."""
    mins, hours, dows = parse_cron(expr)
    now = now.astimezone(timezone.utc)
    for back in range(0, 9):
        day = (now - timedelta(days=back)).date()
        if (day.weekday() + 1) % 7 not in dows:
            continue
        for h in sorted(hours, reverse=True):
            for m in sorted(mins, reverse=True):
                t = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc)
                if t <= now:
                    return t
    return None


def due_slots(schedule, state, now):
    window = timedelta(hours=float(schedule.get("catchup_hours", 3)))
    out = []
    for s in schedule["slots"]:
        t = last_occurrence(s["cron"], now)
        if t is None or now - t > window:
            continue
        if (state.get("done") or {}).get(s["id"]) == iso(t):
            continue
        out.append((s, t))
    return out


# ── ficheros locales ──────────────────────────────────────────────────────────────────────────────────────
def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_token(path=TOKEN_PATH, use_env=True):
    t = os.environ.get("G8_DISPATCH_TOKEN", "").strip() if use_env else ""   # el env solo vale para el repo principal
    if t:
        return t
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


class Log(object):
    def __init__(self, root, now):
        self.root, self.now = root, now
        os.makedirs(os.path.join(root, "logs"), exist_ok=True)

    def info(self, msg):
        line = "%s %s" % (iso(self.now), msg)
        print(line)
        with open(os.path.join(self.root, "logs", "dispatch_%s.log" % self.now.strftime("%Y-%m")), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def alert(self, msg):
        self.info("AVISO " + msg)
        with open(os.path.join(self.root, "logs", "ALERTAS.log"), "a", encoding="utf-8") as fh:
            fh.write("%s 🟠 Mac lanzador: %s\n" % (self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), msg))


# ── GitHub ────────────────────────────────────────────────────────────────────────────────────────────────
def gh(method, path, token, payload=None, transport=None):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "g8-dispatch", "Authorization": "Bearer " + token}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        h["Content-Type"] = "application/json"
    return g8http.fetch(API + path, provider="github", method=method, headers=h, body=body,
                        budget=g8http.Budget(60), transport=transport, read_timeout=30)


def days_left(headers, now):
    exp = ghpublish.parse_expiration((headers or {}).get("github-authentication-token-expiration"))
    return None if exp is None else round((exp - now).total_seconds() / 86400.0, 1)


def check_token(schedule, token, now, transport=None):
    """GET de la lista de workflows: prueba el permiso Actions de lectura sin lanzar nada."""
    res = gh("GET", "/repos/%s/actions/workflows?per_page=1" % schedule["repo"], token, transport=transport)
    return res.ok, res.cls, days_left(res.headers, now)


def load_schedules(root):
    """dispatch_schedule.json (obligatorio) + dispatch_schedule_<nombre>.json (opcionales, orden alfabético)."""
    main_s = read_json(os.path.join(root, "dispatch_schedule.json"), None)
    if not main_s:
        return None
    out = [main_s]
    for n in sorted(os.listdir(root)):
        if n.startswith("dispatch_schedule_") and n.endswith(".json"):
            extra = read_json(os.path.join(root, n), None)
            if extra and extra.get("repo") and extra.get("token_path") and extra.get("slots"):   # nunca el token principal
                out.append(extra)
    return out


def main(argv=None, now=None, transport=None, root=HERE, token_path=TOKEN_PATH):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="muestra qué lanzaría, sin llamar a GitHub ni guardar")
    ap.add_argument("--check", action="store_true", help="solo valida el token y muestra los turnos")
    ap.add_argument("--now", help="instante UTC simulado (pruebas), p. ej. 2026-10-06T13:20:00Z")
    a = ap.parse_args(argv)
    if a.now:
        now = datetime.strptime(a.now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = now or utcnow()
    log = Log(root, now)
    schedules = load_schedules(root)
    if not schedules:
        log.alert("falta o es ilegible dispatch_schedule.json — no se lanza nada")
        return 1
    state_path = os.path.join(root, "state", "dispatch_state.json")
    rc = 0
    for i, schedule in enumerate(schedules):
        # el horario principal usa token_path (parámetro); los demás, su propio "token_path"
        tp = token_path if i == 0 else schedule["token_path"]
        r = run_schedule(a, schedule, state_path, now, log, transport, tp, primary=(i == 0))
        if r == 1 or (r == 2 and rc == 0):
            rc = r
    return rc


def run_schedule(a, schedule, state_path, now, log, transport, token_path, primary=True):
    state = read_json(state_path, {})
    due = due_slots(schedule, state, now)

    if a.dry_run:
        for s, t in due:
            log.info("DRY lanzaría %s (%s, turno %s)" % (s["id"], s["workflow"], iso(t)))
        if not due:
            log.info("DRY nada pendiente")
        return 0

    token = read_token(token_path, use_env=primary)
    if not token:
        if a.check or due:
            log.alert("no hay token (%s): los workflows esperan al cron de GitHub (4–8 h tarde)" % token_path)
        return 1

    if a.check:
        ok, cls, left = check_token(schedule, token, now, transport)
        log.info("%s · token %s · caduca en %s días · %d turnos" % (schedule["repo"], "válido" if ok else "NO válido (%s)" % cls,
                                                                    "?" if left is None else left, len(schedule["slots"])))
        for s in schedule["slots"]:
            log.info("  %-16s %-20s %-14s %s" % (s["id"], s["workflow"], s["cron"], json.dumps(s.get("inputs") or {})))
        if not ok:
            return 1
        return 2 if left is not None and left <= EXPIRY_WARN_DAYS else 0

    if not due:
        return 0
    rc = 0
    warned = state.get("expiry_warned")
    for s, t in due:
        payload = {"ref": schedule.get("ref", "main")}
        if s.get("inputs"):
            payload["inputs"] = s["inputs"]
        res = gh("POST", "/repos/%s/actions/workflows/%s/dispatches" % (schedule["repo"], s["workflow"]),
                 token, payload, transport)
        if res.ok:
            state.setdefault("done", {})[s["id"]] = iso(t)
            write_json(state_path, state)                    # solo tras el 2xx: un fallo se reintenta
            log.info("lanzado %s (%s, turno %s)" % (s["id"], s["workflow"], iso(t)))
            left = days_left(res.headers, now)
            if left is not None and left <= EXPIRY_WARN_DAYS and warned != now.date().isoformat():
                warned = now.date().isoformat()
                log.alert("el token de lanzamiento caduca en %s días: renuévalo en GitHub" % left)
                state["expiry_warned"] = warned
                write_json(state_path, state)
        else:
            rc = 1
            msg = "no se pudo lanzar %s (%s): %s %s" % (s["id"], s["workflow"], res.cls, res.detail or "")
            if res.cls in (g8http.FAIL_AUTH, g8http.FAIL_PERMISSION):
                log.alert(msg + " — revisa el token (Actions: Read and write en este repo)")
            else:
                log.info(msg + " — se reintenta en la próxima pasada")
    return rc


if __name__ == "__main__":
    sys.exit(main())

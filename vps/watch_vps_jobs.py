#!/usr/bin/env python3
"""watch_vps_jobs.py — aviso Telegram si un job VPS (SNB/nzd-b2) falla 2 veces seguidas (t334u).

t342u: también publica data/_ingest/vps_units_status.json (unidades systemd en fallo
y líneas FALLÓ / INCIDENCIA DIRTY_RESET de ALERTAS.log, más el desfase del código
ejecutado vs origin/main) para que ingest_watch las cuente como incidencias.
El push es por Contents API, sin tocar el clon de trabajo compartido.

Se lanza como segundo ExecStart del timer g8-watch-prod. No toca watch_prod.py ni el dispatcher.
Respeta G8_NO_SEND=1. Nunca imprime tokens.
"""
from __future__ import annotations

import argparse
import base64
import re
import subprocess
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_STATE_DIR = "/srv/g8/prod/state/vps_jobs"
DEFAULT_ALERTS = "/srv/g8/prod/state/vps_job_alerts.json"
JOBS = ("snb1a", "nzd-b2")
ALERTS_LOG = "/srv/g8/logs/ALERTAS.log"
STATUS_REPO = "sanderdayan1982/g8-macro-pipeline"
STATUS_PATH = "data/_ingest/vps_units_status.json"
STATUS_SCHEMA = "G8_VPS_UNITS/1"
STATUS_HEARTBEAT_SEC = 3 * 3600
FALLO_KEEP_SEC = 48 * 3600
G8_UNITS = (
    "g8-snb-daily.service",
    "g8-nzd-b2-daily.service",
    "g8-dispatch-prod.service",
    "g8-watch-prod.service",
    "g8-repo-sync.service",
    "g8-private-pull.service",
)
FALLO_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+.*FALLÓ.*"
)
INCIDENCIA_DIRTY_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+.*INCIDENCIA\s+DIRTY_RESET\s+(\S+)\s+(.*)$"
)
UNIT_RE = re.compile(r"\b([A-Za-z0-9_@.-]+\.service)\b")
CODE_LAG_MAX_SEC = 3600
INFRA_REPO = "sanderdayan1982/g8-macro-data"
INFRA_BIN = "infra/vps/bin"
DEFAULT_BIN_DIR = "/srv/g8/bin"
DEFAULT_LAG_STATE = "/srv/g8/prod/state/code_lag.json"


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def telegram(text, transport=None):
    if os.environ.get("G8_NO_SEND") == "1":
        return False, None
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        print("telegram: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID", file=__import__("sys").stderr)
        return False, None
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    try:
        if transport:
            return transport(data)
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
        mid = body.get("result", {}).get("message_id")
        return True, mid
    except Exception as e:
        print("telegram: fallo de envío (%s)" % type(e).__name__, file=__import__("sys").stderr)
        return False, None



def _run_cmd(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout or "", r.stderr or ""


def list_failed_units(units=G8_UNITS, runner=None):
    """Return list of {unit, result, rc} for g8 units in systemd failed state."""
    runner = runner or _run_cmd
    failed = []
    for unit in units:
        rc, out, err = runner(["systemctl", "is-failed", "--", unit])
        state = (out or err or "").strip().splitlines()
        state = state[0].strip() if state else ""
        if state != "failed":
            rc2, out2, _ = runner(["systemctl", "show", "-p", "ActiveState", "--value", "--", unit])
            if (out2 or "").strip() != "failed":
                continue
        rec = {"unit": unit, "result": "failed", "rc": None}
        _, out3, _ = runner(["systemctl", "show", "-p", "Result", "-p", "ExecMainStatus", "--", unit])
        for line in (out3 or "").splitlines():
            if line.startswith("Result="):
                rec["result"] = line.split("=", 1)[1].strip() or rec["result"]
            elif line.startswith("ExecMainStatus="):
                try:
                    rec["rc"] = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
        failed.append(rec)
    return failed


def parse_fallo_lines(text, now_utc, keep_sec=FALLO_KEEP_SEC):
    """Parse FALLÓ lines from ALERTAS.log. No IPs or secrets; only utc, unit, short text."""
    try:
        now = datetime.fromisoformat(str(now_utc).replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        now = datetime.now(timezone.utc)
    out = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", raw)
        inc = INCIDENCIA_DIRTY_RE.match(line)
        m = FALLO_RE.match(line)
        if not inc and not m:
            continue
        utc = (inc or m).group(1)
        try:
            ts = datetime.fromisoformat(utc.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).total_seconds() > keep_sec:
                continue
        except ValueError:
            continue
        if inc:
            unit = "dirty_reset:" + inc.group(2)
        else:
            um = UNIT_RE.search(line)
            unit = um.group(1) if um else "unknown.service"
        short = line
        if " — " in short:
            short = short.split(" — ", 1)[0]
        short = re.sub(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+", "", short)
        short = re.sub(r"^[🔴🟠🟢]\s+", "", short)
        out.append({"utc": utc, "unit": unit, "text": short[:240]})
    return out


def build_status(failed_units, fallo_lines, now_utc, code_lag=None):
    return {
        "schema": STATUS_SCHEMA,
        "generated_utc": now_utc,
        "failed_units": failed_units,
        "fallo_lines": fallo_lines,
        "code_lag": code_lag or [],
    }


def status_payload_key(status):
    """Compare content ignoring the timestamp, for heartbeat-vs-change pushes."""
    return json.dumps({
        "failed_units": status.get("failed_units") or [],
        "fallo_lines": status.get("fallo_lines") or [],
        "code_lag": status.get("code_lag") or [],
    }, sort_keys=True, ensure_ascii=False)


def should_push(new_status, prev_status, now, heartbeat_sec=STATUS_HEARTBEAT_SEC):
    if not prev_status:
        return True, "missing"
    if status_payload_key(new_status) != status_payload_key(prev_status):
        return True, "changed"
    gen = prev_status.get("generated_utc")
    try:
        ts = datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() >= heartbeat_sec:
            return True, "heartbeat"
    except (ValueError, TypeError):
        return True, "bad_prev_ts"
    return False, "unchanged"


def _api_json(token, method, path, payload=None, transport=None):
    url = "https://api.github.com" + path
    data = None if payload is None else json.dumps(payload).encode()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "User-Agent": "g8-vps-watch",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if transport:
        return transport(method, path, data)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw[:200]}
        return e.code, parsed


def push_status_api(status, token, prev=None, transport=None, repo=STATUS_REPO, path=STATUS_PATH):
    """Commit the status file via Contents API. Never prints the token."""
    now = datetime.fromisoformat(status["generated_utc"].replace("Z", "+00:00"))
    do, why = should_push(status, prev, now)
    if not do:
        return {"pushed": False, "reason": why, "sha": None}
    if not token:
        raise RuntimeError("G8_FULL ausente")
    content = (json.dumps(status, ensure_ascii=False, indent=2) + "\n").encode()
    b64 = base64.b64encode(content).decode()
    last_err = None
    for _ in range(3):
        st, cur = _api_json(token, "GET", "/repos/%s/contents/%s?ref=main" % (repo, path), transport=transport)
        if st == 200:
            sha = cur.get("sha")
        elif st == 404:
            sha = None
        else:
            last_err = "GET status %s" % st
            continue
        payload = {
            "message": "[vps-push] vps units status %s [skip netlify]" % status["generated_utc"],
            "content": b64,
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha
        st2, body = _api_json(token, "PUT", "/repos/%s/contents/%s" % (repo, path), payload, transport=transport)
        if st2 in (200, 201):
            new_sha = ((body.get("commit") or {}).get("sha")
                       or (body.get("content") or {}).get("sha"))
            return {"pushed": True, "reason": why, "sha": new_sha}
        last_err = "PUT status %s" % st2
    raise RuntimeError("no se pudo publicar vps_units_status: %s" % last_err)



def is_backup_name(name):
    """Skip leftover backups (*.bak and *.bak-t342u-…, t345u)."""
    n = (name or "").split("/")[-1]
    return n.endswith(".bak") or ".bak." in n or ".bak-" in n


def _basename_only(name):
    name = str(name).replace("\\", "/").rstrip("/")
    return name.rsplit("/", 1)[-1]


def git_blob_sha(path, runner=None):
    runner = runner or _run_cmd
    rc, out, err = runner(["git", "hash-object", "--", path])
    if rc != 0:
        return None
    return (out or "").strip() or None


def list_main_bin_blobs(token, repo=INFRA_REPO, prefix=INFRA_BIN, transport=None):
    """Map basename -> git blob sha from origin/main. Names only; no file bodies stored."""
    if not token:
        return {}
    st, body = _api_json(
        token, "GET",
        "/repos/%s/contents/%s?ref=main" % (repo, prefix),
        transport=transport,
    )
    if st != 200 or not isinstance(body, list):
        return None
    out = {}
    for item in body:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        name = _basename_only(item.get("name") or "")
        sha = item.get("sha")
        if name and sha and not is_backup_name(name):
            out[name] = sha
    return out


def evaluate_code_lag(bin_files, main_blobs, state, now_utc, max_sec=CODE_LAG_MAX_SEC):
    """Return (incidencia list, updated state).

    bin_files: {basename: git_blob_sha or None}
    main_blobs: {basename: git_blob_sha} from origin/main
    Local state remembers first_diff_utc. Only lags older than max_sec are incidencias.
    Public records use basenames only (no VPS paths, no IPs).
    """
    now = datetime.fromisoformat(str(now_utc).replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    state = dict(state or {})
    names = sorted(set(bin_files) | set(main_blobs or {}))
    lag = []
    new_state = {}
    for name in names:
        name = _basename_only(name)
        if not name or is_backup_name(name):
            continue
        bsha = (bin_files or {}).get(name)
        msha = (main_blobs or {}).get(name)
        if bsha and msha and bsha == msha:
            continue
        prev = state.get(name) or {}
        first = prev.get("first_diff_utc")
        if first and prev.get("bin_sha") == bsha and prev.get("main_sha") == msha:
            first_utc = first
        else:
            first_utc = now_utc
        new_state[name] = {"bin_sha": bsha, "main_sha": msha, "first_diff_utc": first_utc}
        try:
            ts = datetime.fromisoformat(str(first_utc).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
        except (ValueError, TypeError):
            age = 0
        if age > max_sec:
            lag.append({
                "name": name,
                "first_diff_utc": first_utc,
                "age_sec": int(age),
            })
    return lag, new_state


def scan_bin_dir(bin_dir, runner=None):
    files = {}
    try:
        names = os.listdir(bin_dir)
    except OSError:
        return files
    for name in names:
        if is_backup_name(name) or name.startswith("."):
            continue
        path = os.path.join(bin_dir, name)
        if not os.path.isfile(path):
            continue
        files[name] = git_blob_sha(path, runner=runner)
    return files


def collect_status(alerts_path, now_utc=None, runner=None, read_text=None,
                   code_lag=None, lag_state=None, lag_state_path=None,
                   bin_dir=None, token=None, transport=None):
    now_utc = now_utc or _utcnow()
    failed = list_failed_units(runner=runner)
    raw = ""
    if read_text is not None:
        raw = read_text(alerts_path) or ""
    else:
        try:
            with open(alerts_path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            raw = ""
    fallo = parse_fallo_lines(raw, now_utc)
    if code_lag is None:
        bin_dir = bin_dir or os.environ.get("G8_VPS_BIN") or DEFAULT_BIN_DIR
        lag_state_path = lag_state_path or os.environ.get("G8_CODE_LAG_STATE") or DEFAULT_LAG_STATE
        if lag_state is None:
            lag_state = read_json(lag_state_path, {})
        token = token if token is not None else os.environ.get("G8_FULL", "").strip()
        blobs = list_main_bin_blobs(token, transport=transport) if token else None
        if blobs is None:
            # sin token o fallo de API: no se inventa un desfase
            code_lag = []
        else:
            bin_files = scan_bin_dir(bin_dir, runner=runner)
            code_lag, lag_state = evaluate_code_lag(bin_files, blobs, lag_state, now_utc)
            if lag_state_path:
                write_json(lag_state_path, lag_state)
    return build_status(failed, fallo, now_utc, code_lag=code_lag)


def evaluate(state_dir, alerts, jobs=JOBS, now=None):
    """Return (nuevo_aviso list, alerts actualizado). Alerta solo al pasar a >=2 fallos seguidos."""
    now = now or _utcnow()
    nuevos = []
    for job in jobs:
        rec = read_json(os.path.join(state_dir, "%s.json" % job), {})
        streak = int(rec.get("consecutive_fail") or 0)
        ok = bool(rec.get("ok"))
        key = "fail2:%s" % job
        if ok or streak < 2:
            alerts.pop(key, None)
            continue
        if key in alerts:
            continue
        err = rec.get("error") or rec.get("error_type") or "rc=%s" % rec.get("rc")
        text = ("G8 VPS · job <b>%s</b> falló 2 veces seguidas (%s). "
                "Último: %s" % (job, err, rec.get("utc") or now))
        nuevos.append((job, text))
        alerts[key] = {"utc": now, "streak": streak}
    return nuevos, alerts


def main(argv=None, transport_tg=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=os.environ.get("G8_VPS_JOB_DIR", DEFAULT_STATE_DIR))
    ap.add_argument("--alerts", default=os.environ.get("G8_VPS_JOB_ALERTS", DEFAULT_ALERTS))
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args(argv)
    alerts = read_json(args.alerts, {})
    nuevos, alerts = evaluate(args.state_dir, alerts)
    sent = []
    for job, text in nuevos:
        if args.no_send:
            ok, mid = False, None
        else:
            ok, mid = telegram(text, transport=transport_tg)
        sent.append({"job": job, "telegram_ok": ok, "message_id": mid})
        print("AVISO %s (telegram ok=%s)" % (text, str(ok).lower()))
    write_json(args.alerts, alerts)
    print("watch_vps_jobs avisos_nuevos=%d abiertos=%d" % (len(nuevos), len(alerts)))

    # t342u: estado de unidades para ingest_watch (no toca el clon de trabajo).
    local_state = os.environ.get("G8_VPS_UNITS_STATUS", "/srv/g8/prod/state/vps_units_status.json")
    alerts_log = os.environ.get("G8_ALERTAS_LOG", ALERTS_LOG)
    try:
        status = collect_status(alerts_log)
        write_json(local_state, status)
        prev = None
        token = os.environ.get("G8_FULL", "").strip()
        if token and not args.no_send:
            # previous published content is unknown locally; use last local if present
            prev = read_json(local_state + ".published", {})
            push = push_status_api(status, token, prev=prev or None)
            if push.get("pushed"):
                write_json(local_state + ".published", status)
            print("vps_units_status pushed=%s reason=%s sha=%s failed=%d fallo_lines=%d" % (
                str(push.get("pushed")).lower(), push.get("reason"), push.get("sha"),
                len(status.get("failed_units") or []), len(status.get("fallo_lines") or [])))
        else:
            print("vps_units_status local-only failed=%d fallo_lines=%d" % (
                len(status.get("failed_units") or []), len(status.get("fallo_lines") or [])))
    except Exception as e:
        print("vps_units_status ERROR %s: %s" % (type(e).__name__, e), file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

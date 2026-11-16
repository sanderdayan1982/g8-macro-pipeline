#!/usr/bin/env python3
"""private_data.py — doble escritura del plan de licencia CME (PR-A, orden Sander 16-nov-2026).

El repo público sigue escribiendo como hoy. Este programa copia las filas privadas
hacia o desde el clon de g8-macro-data. El token solo entra por el entorno
(G8_DATA_TOKEN): nunca por argv, ni por la URL, ni por la salida.

    pull  CLONE --rows 1,4-8     clona (depth 1) y rsync de esas filas al workspace
    push  CLONE --paths ...      copia esas rutas al clon, commit y push con reintento
    redact                       brief/state públicos y data/public/*.json (no lo llama PR-A)
    verify CLONE --paths ...     sha256; imprime solo cuentas y rutas distintas

Filas (plan §1.1): ver ROWS. La enmienda de crédito solo entra con --credit-if-present
y solo si el fichero existe.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

REPO_URL = "https://github.com/sanderdayan1982/g8-macro-data.git"
PAIR = {
    "EUR": "EUR/USD", "JPY": "USD/JPY", "GBP": "GBP/USD",
    "AUD": "AUD/USD", "CAD": "USD/CAD", "CHF": "USD/CHF", "NZD": "NZD/USD",
}
FUTURES = ("6A", "6B", "6C", "6E", "6J", "6N", "6S")
ROWS = {
    1: ["data/options/canonical"],
    2: ["data/options/volume_shadow"],
    3: ["data/options/backfill_report.json", "data/options/volume_backfill_report.json"],
    4: ["data/options/shadow_history.json"],
    5: ["data/options/fxvol_history.csv"],
    6: ["data/OPTIONS_SURFACE.json"],
    7: ["data/CME_OPTIONS_C1_shadow.json"],
    8: ["data/CME_FXVOL_shadow.json"],
    9: ["data/futures/canonical/%s.csv" % name for name in FUTURES],
    10: ["data/futures/canonical/DX.csv"],
    11: ["data/alerts/brief.json"],
    12: ["data/alerts/state.json"],
    13: ["tests/fixtures/fxvol/FXVOL_test_vectors.json"],
}
CREDIT = ["data/credit/CRD_shadow.json", "data/credit/oas_vintage.csv"]
CME_STATUS_FILES = [
    "data/OPTIONS_SURFACE.json",
    "data/CME_OPTIONS_C1_shadow.json",
    "data/CME_FXVOL_shadow.json",
]
FXVOL_FILE = "data/CME_FXVOL_shadow.json"


def safe_rel(path):
    rel = path.strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or rel.startswith("/"):
        sys.stderr.write("private_data: ruta no permitida\n")
        sys.exit(2)
    return "/".join(parts)


def parse_rows(text):
    if not text:
        return []
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            lo, hi = int(left), int(right)
            if lo > hi or lo not in ROWS or hi not in ROWS:
                sys.stderr.write("private_data: fila fuera de 1..13\n")
                sys.exit(2)
            out.extend(range(lo, hi + 1))
        else:
            n = int(part)
            if n not in ROWS:
                sys.stderr.write("private_data: fila fuera de 1..13\n")
                sys.exit(2)
            out.append(n)
    seen = []
    for n in out:
        if n not in seen:
            seen.append(n)
    return seen


def paths_of(rows, extra):
    out = []
    for n in rows:
        for rel in ROWS[n]:
            rel = safe_rel(rel)
            if rel not in out:
                out.append(rel)
    for rel in extra or []:
        rel = safe_rel(rel)
        if rel not in out:
            out.append(rel)
    return out


def scrub(text, token):
    if not text:
        return ""
    if token:
        text = text.replace(token, "***")
    return text


def token_from_env():
    return os.environ.get("G8_DATA_TOKEN", "")


def install_askpass(parent):
    path = os.path.join(parent, ".g8-askpass")
    # El valor no se escribe aquí: git lee $G8_DATA_TOKEN cuando pregunta la contraseña.
    body = """#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s\\n' "x-access-token" ;;
  *) printf '%s\\n' "$G8_DATA_TOKEN" ;;
esac
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o700)
    return path


def git_env(token, askpass):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    if token:
        env["G8_DATA_TOKEN"] = token
    if askpass:
        env["GIT_ASKPASS"] = askpass
        env["SSH_ASKPASS"] = askpass
        env["GIT_ASKPASS_REQUIRE"] = "force"
    return env


def git(args, cwd, env, token):
    cmd = ["git", "-c", "credential.helper="] + list(args)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    err = scrub(proc.stderr, token)
    if proc.returncode != 0 and err.strip():
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    return proc.returncode


def git_text(args, cwd, env, token):
    cmd = ["git", "-c", "credential.helper="] + list(args)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return proc.returncode, scrub(proc.stdout, token)


def ensure_clone(dest, repo, token):
    dest = os.path.abspath(dest)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    if repo.startswith("https://") and "@" in repo:
        sys.stderr.write("private_data: la URL no puede llevar credenciales\n")
        sys.exit(2)
    ask = install_askpass(parent) if token or repo.startswith("https://") else None
    if repo.startswith("https://") and not token:
        sys.stderr.write("private_data: G8_DATA_TOKEN is not set\n")
        sys.exit(1)
    env = git_env(token, ask)
    git_dir = os.path.join(dest, ".git")
    if os.path.isdir(git_dir):
        return dest, env
    if os.path.exists(dest):
        sys.stderr.write("private_data: la ruta del clon no es un checkout\n")
        sys.exit(1)
    rc = git(["clone", "-q", "--depth", "1", "--branch", "main", repo, dest], parent, env, token)
    if rc != 0:
        sys.stderr.write("private_data: clone rc=%s\n" % rc)
        sys.exit(rc or 1)
    return dest, env


def rsync_path(src, dst):
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        cmd = ["rsync", "-a", src.rstrip("/") + "/", dst.rstrip("/") + "/"]
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        cmd = ["rsync", "-a", src, dst]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write("private_data: rsync rc=%s\n" % proc.returncode)
        sys.exit(proc.returncode or 1)


def copy_paths(paths, src_root, dst_root, optional):
    copied, missing = [], []
    for rel in paths:
        src = os.path.join(src_root, rel)
        dst = os.path.join(dst_root, rel)
        if not os.path.exists(src):
            missing.append(rel)
            continue
        rsync_path(src, dst)
        copied.append(rel)
    required = [rel for rel in missing if rel not in optional]
    print("copied=%s missing=%s" % (len(copied), len(missing)))
    for rel in missing:
        print(rel)
    return required


def prepare_clone(clone, env, token):
    git(["config", "user.name", "github-actions[bot]"], clone, env, token)
    git(["config", "user.email", "github-actions[bot]@users.noreply.github.com"], clone, env, token)
    helper = os.path.join(clone, "scripts", "tools", "votes_csv_merge.py")
    if not os.path.isfile(helper):
        return
    info = os.path.join(clone, ".git", "info")
    os.makedirs(info, exist_ok=True)
    attr = os.path.join(info, "attributes")
    line = "data/votes.csv merge=votesAppend\n"
    current = ""
    if os.path.exists(attr):
        with open(attr, encoding="utf-8") as fh:
            current = fh.read()
    if line not in current:
        with open(attr, "a", encoding="utf-8") as fh:
            fh.write(line)
    git(["config", "merge.votesAppend.name", "votes.csv append-only by key"], clone, env, token)
    driver = 'python3 "%s" %%O %%A %%B' % helper
    git(["config", "merge.votesAppend.driver", driver], clone, env, token)


def maybe_unshallow(clone, env, token):
    rc, out = git_text(["rev-parse", "--is-shallow-repository"], clone, env, token)
    if rc == 0 and out.strip() == "true":
        git(["fetch", "-q", "--unshallow", "origin"], clone, env, token)


def push_retry(clone, env, token, retries, pause):
    maybe_unshallow(clone, env, token)
    for i in range(1, retries + 1):
        print("push: intento %s de %s" % (i, retries))
        rc = git(["pull", "-q", "--rebase", "-X", "theirs", "origin", "main"], clone, env, token)
        if rc == 0:
            rc = git(["push", "origin", "HEAD:main"], clone, env, token)
            if rc == 0:
                print("✓ push correcto en el intento %s" % i)
                return 0
        else:
            git(["rebase", "--abort"], clone, env, token)
            print("✗ no se pudo integrar main en el intento %s" % i)
        if i < retries and pause:
            time.sleep(i * pause)
    print("✗ push fallido tras %s intentos" % retries)
    return 1


def cmd_pull(args, token):
    rows = parse_rows(args.rows)
    paths = paths_of(rows, [])
    if not paths:
        sys.stderr.write("private_data: pull sin filas\n")
        return 2
    clone, _env = ensure_clone(args.clone, args.repo, token)
    missing = copy_paths(paths, clone, os.path.abspath(args.workspace), optional=())
    return 1 if missing else 0


def cmd_push(args, token):
    rows = parse_rows(args.rows)
    extra = list(args.paths or [])
    if args.credit_if_present:
        root = os.path.abspath(args.workspace)
        for rel in CREDIT:
            if os.path.exists(os.path.join(root, rel)):
                extra.append(rel)
        print("credit=%s" % sum(1 for rel in CREDIT if os.path.exists(os.path.join(root, rel))))
    paths = paths_of(rows, extra)
    if not paths:
        sys.stderr.write("private_data: push sin rutas\n")
        return 2
    if not args.workflow or not args.session or "\n" in args.workflow or "\n" in args.session:
        sys.stderr.write("private_data: hacen falta --workflow y --session\n")
        return 2
    clone, env = ensure_clone(args.clone, args.repo, token)
    prepare_clone(clone, env, token)
    missing = copy_paths(paths, os.path.abspath(args.workspace), clone, optional=tuple(CREDIT))
    if missing:
        return 1
    for rel in paths:
        if os.path.exists(os.path.join(clone, rel)):
            rc = git(["add", "--", rel], clone, env, token)
            if rc != 0:
                return rc or 1
    if git(["diff", "--cached", "--quiet"], clone, env, token) == 0:
        git(["reset", "-q"], clone, env, token)
        print("private_data push: no-op")
        return 0
    msg = "data: %s %s [skip netlify]" % (args.workflow, args.session)
    rc = git(["commit", "-m", msg], clone, env, token)
    if rc != 0:
        return rc or 1
    return push_retry(clone, env, token, args.retries, args.retry_sleep)


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def wall_public(item, session):
    if not isinstance(item, dict):
        return {"pair": None, "status": "NA", "session": session}
    pair = item.get("pair")
    status = item.get("status")
    if not isinstance(status, str):
        gate = item.get("gate0")
        status = gate if isinstance(gate, str) else "NA"
    sess = item.get("session") if isinstance(item.get("session"), str) else session
    return {"pair": pair, "status": status, "session": sess}


def walls_public(walls, session):
    if isinstance(walls, list):
        return [wall_public(item, session) for item in walls]
    if not isinstance(walls, dict):
        return []
    sess = walls.get("session") if isinstance(walls.get("session"), str) else session
    out = []
    for key, val in walls.items():
        if key == "session":
            continue
        if isinstance(val, dict):
            item = {"pair": val.get("pair") or PAIR.get(key, key)}
            if isinstance(val.get("status"), str):
                item["status"] = val["status"]
            elif isinstance(val.get("gate0"), str):
                item["status"] = val["gate0"]
            item["session"] = val.get("session") if isinstance(val.get("session"), str) else sess
            out.append(wall_public(item, sess))
        else:
            out.append({"pair": PAIR.get(key, key), "status": "NA", "session": sess})
    return out


def status_entry(doc):
    session = doc.get("session") if isinstance(doc.get("session"), str) else None
    if session is None and isinstance(doc.get("latest_session"), str):
        session = doc["latest_session"]
    generated = doc.get("generated_utc") if isinstance(doc.get("generated_utc"), str) else None
    status = doc.get("status") if isinstance(doc.get("status"), str) else None
    if status is None and isinstance(doc.get("mode"), str):
        status = doc["mode"]
    return {"session": session, "generated_utc": generated, "status": status}


def fxvol_dates(doc):
    window = doc.get("fut_roll_window") if isinstance(doc.get("fut_roll_window"), dict) else {}
    opex = {}
    currencies = doc.get("currencies") if isinstance(doc.get("currencies"), dict) else {}
    for ccy, row in currencies.items():
        if not isinstance(row, dict):
            continue
        opex[ccy] = {"exp1": row.get("exp1"), "dte1": row.get("dte1")}
    return {
        "session": doc.get("session") if isinstance(doc.get("session"), str) else None,
        "fut_roll_window": {
            "start": window.get("start"),
            "end": window.get("end"),
            "active": window.get("active"),
            "imm": window.get("imm"),
        },
        "opex": opex,
    }


def reduce_options_surface(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("status"), str):
        return value["status"]
    return "NA"


def cmd_redact(args):
    root = os.path.abspath(args.workspace)
    brief_path = os.path.join(root, "data", "alerts", "brief.json")
    state_path = os.path.join(root, "data", "alerts", "state.json")
    for path in (brief_path, state_path, os.path.join(root, FXVOL_FILE)):
        if not os.path.isfile(path):
            sys.stderr.write("private_data: falta %s\n" % os.path.relpath(path, root))
            return 1
    brief = load_json(brief_path)
    state = load_json(state_path)
    as_of = brief.get("as_of") if isinstance(brief.get("as_of"), dict) else {}
    options_date = as_of.get("options") if isinstance(as_of.get("options"), str) else None
    brief["walls"] = walls_public(brief.get("walls"), options_date)
    if isinstance(brief.get("as_of"), dict):
        brief["as_of"]["options"] = options_date
    state_session = None
    if isinstance(state.get("walls"), dict) and isinstance(state["walls"].get("session"), str):
        state_session = state["walls"]["session"]
    state["walls"] = walls_public(state.get("walls"), state_session or options_date)
    dqm = state.get("dqm")
    if isinstance(dqm, dict) and "OPTIONS_SURFACE" in dqm:
        dqm["OPTIONS_SURFACE"] = reduce_options_surface(dqm["OPTIONS_SURFACE"])
    dump_json(brief_path, brief)
    dump_json(state_path, state)
    public = {}
    for rel in CME_STATUS_FILES:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            sys.stderr.write("private_data: falta %s\n" % rel)
            return 1
        public[rel] = status_entry(load_json(path))
    dump_json(os.path.join(root, "data", "public", "cme_status.json"), public)
    dump_json(os.path.join(root, "data", "public", "fxvol_dates.json"), fxvol_dates(load_json(os.path.join(root, FXVOL_FILE))))
    print("redact=4")
    return 0


def files_under(root, rel):
    path = os.path.join(root, rel)
    if os.path.isfile(path):
        return [rel]
    if not os.path.isdir(path):
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            found.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return found


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def cmd_verify(args, token):
    rows = parse_rows(args.rows)
    extra = list(args.paths or [])
    if getattr(args, "credit_if_present", False):
        workspace = os.path.abspath(args.workspace)
        clone = os.path.abspath(args.clone)
        for rel in CREDIT:
            if os.path.exists(os.path.join(workspace, rel)) or os.path.exists(os.path.join(clone, rel)):
                extra.append(rel)
    paths = paths_of(rows, extra)
    if not paths:
        sys.stderr.write("private_data: verify sin rutas\n")
        return 2
    clone = os.path.abspath(args.clone)
    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(os.path.join(clone, ".git")):
        # verify no clona: el pull de este job ya dejó el checkout. Sin red.
        del token
        sys.stderr.write("private_data: no está el clon\n")
        return 1
    compared = same = 0
    differ = []
    seen = []
    for rel in paths:
        names = sorted(set(files_under(workspace, rel) + files_under(clone, rel)))
        if not names:
            compared += 1
            differ.append(rel)
            continue
        for name in names:
            if name in seen:
                continue
            seen.append(name)
            compared += 1
            left = os.path.join(workspace, name)
            right = os.path.join(clone, name)
            if os.path.isfile(left) and os.path.isfile(right) and sha256_file(left) == sha256_file(right):
                same += 1
            else:
                differ.append(name)
    print("compared=%s same=%s differ=%s" % (compared, same, len(differ)))
    if differ:
        print("differ:")
        for name in differ:
            print(name)
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="private_data.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull")
    pull.add_argument("clone")
    pull.add_argument("--rows", required=True)
    pull.add_argument("--repo", default=REPO_URL)
    pull.add_argument("--workspace", default=".")

    push = sub.add_parser("push")
    push.add_argument("clone")
    push.add_argument("--paths", nargs="*", default=[])
    push.add_argument("--rows", default="")
    push.add_argument("--workflow", required=True)
    push.add_argument("--session", required=True)
    push.add_argument("--credit-if-present", action="store_true")
    push.add_argument("--repo", default=REPO_URL)
    push.add_argument("--workspace", default=".")
    push.add_argument("--retries", type=int, default=5)
    push.add_argument("--retry-sleep", type=float, default=5)

    redact = sub.add_parser("redact")
    redact.add_argument("--workspace", default=".")

    verify = sub.add_parser("verify")
    verify.add_argument("clone")
    verify.add_argument("--paths", nargs="*", default=[])
    verify.add_argument("--rows", default="")
    verify.add_argument("--credit-if-present", action="store_true")
    verify.add_argument("--workspace", default=".")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    token = token_from_env()
    if args.cmd == "pull":
        return cmd_pull(args, token)
    if args.cmd == "push":
        return cmd_push(args, token)
    if args.cmd == "redact":
        return cmd_redact(args)
    if args.cmd == "verify":
        return cmd_verify(args, token)
    return 2


if __name__ == "__main__":
    sys.exit(main())

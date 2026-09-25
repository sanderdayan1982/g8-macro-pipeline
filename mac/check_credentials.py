#!/usr/bin/env python3
"""check_credentials.py — v1.0 · comprobación manual del token de datos del Mac, sin escribir nada.

Lee ~/.g8/github_token (o $GITHUB_TOKEN), hace UNA lectura del repo y muestra: tipo de token, validez,
caducidad (cabecera GitHub-Authentication-Token-Expiration), permisos (X-OAuth-Scopes en tokens classic)
y si sobran permisos para una tarea que solo sube datos. Nunca imprime el token.
El publicador (push_nzd_to_github.py v2.0) hace la misma comprobación en cada ejecución.
Salida: 0 válido y con permiso · 1 inválido/sin permiso/sin token · 2 caduca en ≤7 días.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from g8common import g8http, ghpublish as G  # noqa: E402

from push_nzd_to_github import OWNER, REPO, BRANCH, read_token  # noqa: E402


def main(repo_factory=None):
    tok = read_token()
    if not tok:
        print("sin token (~/.g8/github_token)")
        return 1
    repo = (repo_factory or (lambda t: G.Repo(OWNER, REPO, BRANCH, token=t, budget=g8http.Budget(60))))(tok)
    r = G.check_token(repo, tok, role="data")
    print(json.dumps(r, indent=1, ensure_ascii=False))
    if not r["valid"] or r.get("required_ok") is False:
        return 1
    if r.get("days_left") is not None and r["days_left"] <= 7:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

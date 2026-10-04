#!/usr/bin/env python3
"""agent_gate.py — acta P-6: compuerta del agente de mantenimiento (la decide el workflow, no el agente).

    python scripts/tools/agent_gate.py --base <sha>      → JSON en stdout; rc 0 = AUTO, 3 = OWNER, 4 = NADA

AUTO  : todos los ficheros cambiados entre <base> y HEAD están en la lista permitida (descargadores, registro de
        fuentes, decisiones verificadas, proxy del dashboard, actas y tests del agente). El workflow los integra en
        main si además la suite y las comprobaciones de Validate pasan.
OWNER : algún fichero fuera de la lista (modelos, umbrales, motor de alertas, workflows, dashboard…) → PR para el
        propietario. Nunca se integra solo.
NADA  : el agente no hizo commits.
"""
import argparse
import csv
import fnmatch
import io
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

ALLOW = [
    "scripts/fetch_*.py",
    "scripts/g8common/cb_direct.py",
    "sources/registry.csv",
    "data/manual/policy_decisions.csv",
    "docs/_redirects",
    "docs/actas/ACTA_AGENTE_*.md",
    "tests/test_agent_*.py",
    "tests/fixtures/agent/*",
    "sources/alternatives/*.json",
]
# Nunca AUTO aunque un patrón de ALLOW lo cubriera (metodología o huellas congeladas).
DENY = [
    "scripts/acm_g8.py", "scripts/s01b.py", "scripts/dashboard_alerts.py", "scripts/usd_factor.py",
    "scripts/book_risk.py", "scripts/metals_fairvalue_g8.py", "scripts/nzd_tp_synth.py", "scripts/real_yields_g8.py",
    "scripts/aud_nowcast.py", "scripts/tools/*", "scripts/health_monitor.py",
    "sources/freshness_*.csv", "sources/nowcast_aud.json", "tests/test_p8_aud_nowcast.py", "tests/test_exclusions.py", "tests/test_f3_freshness.py", "tests/frozen_data.py",
    ".github/*",
]


def allowed(path):
    if any(fnmatch.fnmatch(path, p) for p in DENY):
        return False
    return any(fnmatch.fnmatch(path, p) for p in ALLOW)


def classify(paths):
    paths = sorted(set(p for p in paths if p))
    if not paths:
        return {"decision": "NADA", "changed": [], "outside": []}
    outside = [p for p in paths if not allowed(p)]
    return {"decision": "OWNER" if outside else "AUTO", "changed": paths, "outside": outside}


def changed_since(base):
    out = subprocess.run(["git", "diff", "--name-only", base, "HEAD"], capture_output=True, text=True, check=True)
    return out.stdout.split("\n")


def validate_diff(base, result):
    """Trusted-base gate: only regular files, no removal, unchanged data semantics.

    Runs from a copy of this file captured BEFORE checking out candidate code.
    This is a permission gate, not proof that an alternative source is equivalent.
    """
    def git(*args):
        return subprocess.run(["git"] + list(args), capture_output=True, text=True, check=True).stdout
    reasons = []
    for line in git("diff", "--raw", "--no-renames", base, "HEAD").splitlines():
        meta, path = line.split("\t", 1)
        mode = meta.split()[1]
        if mode != "100644":
            reasons.append("non-regular/deleted file: " + path)
    if "sources/registry.csv" in result["changed"]:
        old = {r["feed_id"]: r for r in csv.DictReader(io.StringIO(git("show", base + ":sources/registry.csv")))}
        new = {r["feed_id"]: r for r in csv.DictReader(io.StringIO(git("show", "HEAD:sources/registry.csv")))}
        editable = {"primary_source", "primary_access", "fallback_source", "fallback_access", "notes"}
        if old.keys() != new.keys() or any(
            old[k].get(col) != new[k].get(col) for k in old.keys() & new.keys() for col in old[k] if col not in editable
        ):
            reasons.append("registry semantics/budgets changed")
    code = [p for p in result["changed"] if p.startswith("scripts/") or p == "sources/registry.csv"]
    if code:
        if not any(p.startswith("tests/test_agent_") for p in result["changed"]):
            reasons.append("repair needs a regression test")
        if not any(p.startswith("docs/actas/ACTA_AGENTE_") for p in result["changed"]):
            reasons.append("repair needs source evidence in an acta")
    manifests = [p for p in result["changed"] if p.startswith("sources/alternatives/") and p.endswith(".json")]
    verified_urls = set()
    official = {"bankofengland.co.uk", "ecb.europa.eu", "bundesbank.de", "newyorkfed.org", "stlouisfed.org",
                "federalreserve.gov", "treasury.gov", "boj.or.jp", "mof.go.jp", "jsda.or.jp", "bankofcanada.ca",
                "rba.gov.au", "rbnz.govt.nz", "snb.ch", "bis.org", "cftc.gov"}
    for path in manifests:
        try:
            m = json.loads(git("show", "HEAD:" + path))
            for key in ("feed_id", "checked_utc", "old_url", "new_url", "documentation_url", "unit", "currency", "tenor", "frequency", "observation_date"):
                if not m.get(key):
                    raise ValueError("missing " + key)
            for key in ("new_url", "documentation_url"):
                u = urlparse(m[key])
                if u.scheme != "https" or not any(u.hostname == h or (u.hostname or "").endswith("." + h) for h in official):
                    raise ValueError("not a known official HTTPS source")
            checked = datetime.fromisoformat(m["checked_utc"].replace("Z", "+00:00"))
            if checked.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - checked).total_seconds() <= 7 * 86400:
                raise ValueError("verification not current")
            if m.get("same_definition") is not True or int(m.get("overlap_count", 0)) < 20 or float(m.get("max_abs_diff", -1)) != 0:
                raise ValueError("equivalence needs review")
            verified_urls.add(m["new_url"])
        except (ValueError, TypeError, KeyError) as exc:
            reasons.append("source evidence %s: %s" % (path, exc))
    for path in code:
        old_text = git("show", base + ":" + path) if git("ls-tree", "--name-only", base, "--", path).strip() else ""
        new_text = git("show", "HEAD:" + path)
        urls = lambda text: set(re.findall(r'https://[^\s\"\'<>),]+', text))
        added = urls(new_text) - urls(old_text)
        if added - verified_urls:
            reasons.append("new URL without exact verified evidence: " + path)
    result["reasons"] = reasons
    if reasons and result["decision"] != "NADA":
        result["decision"] = "OWNER"
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    a = ap.parse_args(argv)
    res = validate_diff(a.base, classify(changed_since(a.base)))
    print(json.dumps(res, ensure_ascii=False))
    return {"AUTO": 0, "OWNER": 3, "NADA": 4}[res["decision"]]


if __name__ == "__main__":
    sys.exit(main())

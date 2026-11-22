"""t340u: un fetcher en registry.csv debe tener workflow o job VPS/systemd en el repo que lo ejecute.

Evita la regresión de #105 (script fusionado, sin job que lo publique).
No hay listas blancas: si aparece un fetcher sin job, este test falla.
"""
import csv
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FETCH_RE = re.compile(r"(?:scripts/)?(fetch_[a-z0-9_]+\.py)")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _registry_fetchers():
    with open(os.path.join(ROOT, "sources", "registry.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    found = {}
    for r in rows:
        pa = r.get("primary_access") or ""
        m = FETCH_RE.search(pa)
        if not m:
            continue
        script = "scripts/" + m.group(1)
        found.setdefault(script, []).append(r["feed_id"])
    return found


def _job_corpus():
    chunks = []
    wf = os.path.join(ROOT, ".github", "workflows")
    if os.path.isdir(wf):
        for n in os.listdir(wf):
            if n.endswith(".yml") or n.endswith(".yaml"):
                chunks.append(_read(os.path.join(".github", "workflows", n)))
    vps = os.path.join(ROOT, "vps")
    if os.path.isdir(vps):
        for n in os.listdir(vps):
            path = os.path.join(vps, n)
            if os.path.isfile(path):
                try:
                    chunks.append(open(path, encoding="utf-8", errors="ignore").read())
                except OSError:
                    continue
    return "\n".join(chunks)


class RegistryFetchersHaveJobs(unittest.TestCase):
    def test_every_registry_fetch_script_is_run_by_a_job(self):
        corpus = _job_corpus()
        missing = []
        for script, feeds in sorted(_registry_fetchers().items()):
            base = os.path.basename(script)
            module = base[:-3]  # fetch_foo
            if script in corpus or base in corpus or module in corpus:
                continue
            missing.append("%s (feeds: %s)" % (script, ",".join(feeds)))
        self.assertEqual(missing, [], "fetchers en registry.csv sin workflow ni job VPS/systemd en el repo")

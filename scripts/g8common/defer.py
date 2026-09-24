"""defer — aplazamientos compartidos (Retry-After → not_before) por ÁMBITO DE LÍMITE del proveedor (hallazgo #5).

Un proveedor que pide esperar (429/503 con Retry-After, x-ratelimit-reset…) no vuelve a recibir peticiones
antes de ese instante:
  · en llamadas posteriores del MISMO proceso (memoria);
  · en ejecuciones posteriores y en OTROS scripts y workflows del pipeline: cada workflow ESCRIBE solo su fichero
    data/_ingest/not_before/<G8_JOB>.json (lo committea con sus datos; sin conflictos de rebase entre workflows
    de grupos de concurrencia distintos) y TODOS LEEN la unión del directorio;
  · desde cualquier endpoint del mismo ámbito: un endpoint alternativo del mismo proveedor NO elude el límite
    (p. ej. api.stlouisfed.org y fred.stlouisfed.org comparten el ámbito «stlouisfed»).

Ámbito = sufijo de dominio de la tabla SCOPES (el más largo que coincida) o, si no está, el dominio registrable
(dos últimas etiquetas; tres si la penúltima es un segundo nivel genérico de un ccTLD: co.uk, gov.au, or.jp…).
Es una regla conservadora: puede retrasar un endpoint que tenga un límite independiente, nunca adelantarlo.
"""
import json
import os
import time
import urllib.parse

from . import runlog

REL_DIR = os.path.join("data", "_ingest", "not_before")


def job_name(env=None):
    env = os.environ if env is None else env
    j = (env.get("G8_JOB") or "local").strip()
    return "".join(c for c in j if c.isalnum() or c in "-_") or "local"


SCOPES = {
    "stlouisfed.org": "stlouisfed",          # FRED API, fredgraph, ALFRED
    "ecb.europa.eu": "ecb",                  # data-api.ecb.europa.eu (no todo europa.eu)
    "bankofengland.co.uk": "boe",
    "boj.or.jp": "boj",
    "bankofcanada.ca": "boc",
    "rba.gov.au": "rba",
    "bis.org": "bis",
    "rbnz.govt.nz": "rbnz",
    "snb.ch": "snb",
    "newyorkfed.org": "nyfed",
    "treasury.gov": "ustreasury",
    "api.github.com": "github",
    "api.telegram.org": "telegram",
}
_SECOND_LEVEL = {"co", "com", "gov", "govt", "ac", "org", "net", "or", "go", "ne", "edu"}


def scope_for(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    best = None
    for suf, sc in SCOPES.items():
        if host == suf or host.endswith("." + suf):
            if best is None or len(suf) > len(best[0]):
                best = (suf, sc)
    if best:
        return best[1]
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class Store(object):
    """not_before por ámbito. `path` = directorio data/_ingest/not_before. Escribe solo <job>.json; lee la unión.
    Cada set relee y fusiona (varios scripts secuenciales del mismo job comparten su fichero)."""

    def __init__(self, path, now=time.time, job=None):
        self.dir = path
        self.job = job or job_name()
        self.path = os.path.join(path, self.job + ".json")
        self.now = now
        self._mem = {}

    @staticmethod
    def _load(p):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            return {k: v for k, v in d.get("not_before", {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            return {}

    def _read_all(self):
        out = {}
        try:
            names = sorted(n for n in os.listdir(self.dir) if n.endswith(".json"))
        except OSError:
            names = []
        for n in names:
            for k, v in self._load(os.path.join(self.dir, n)).items():
                if (v.get("until") or 0) > (out.get(k, {}).get("until") or 0):
                    out[k] = v
        return out

    def get(self, scope):
        """Epoch hasta el que el ámbito está aplazado (o None)."""
        t = self.now()
        cands = [self._mem.get(scope)]
        rec = self._read_all().get(scope)
        if rec:
            cands.append(rec.get("until"))
        cands = [c for c in cands if c is not None and c > t]
        return max(cands) if cands else None

    def set(self, scope, until, source=""):
        if until is None:
            return
        self._mem[scope] = max(until, self._mem.get(scope) or 0)
        d = self._load(self.path)
        cur = d.get(scope, {}).get("until") or 0
        if until > cur:
            d[scope] = {"until": until, "set_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now())),
                        "source": source[:200]}
        t = self.now()
        d = {k: v for k, v in d.items() if (v.get("until") or 0) > t}          # caducados fuera
        from .series import write_atomic
        write_atomic(self.path, runlog.dumps({"job": self.job, "not_before": d}))

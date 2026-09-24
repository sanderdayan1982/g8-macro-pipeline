"""Arnés de equivalencia para los descargadores adoptados en el lote 3.

Ejecuta, EN PROCESO y sin red:
  · la versión ORIGINAL congelada (tests/fixtures/original/<script>.py, copia exacta de main ed9ed64) con un
    `requests` falso que sirve respuestas grabadas/sintéticas;
  · la versión NUEVA (scripts/<script>.py) con el transporte de g8http sustituido por esas mismas respuestas.
Las respuestas se construyen con el FORMATO DE CADA FUENTE a partir de los CSV reales del repo, así que el
contenido es real y el envoltorio es el que el parser original espera. Un reloj simulado evita esperas.
"""
import csv
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from urllib.parse import urlencode

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

from g8common import ingest as ING  # noqa: E402
from g8fakes import Clock  # noqa: E402

NOW = datetime(2026, 9, 24, 21, 35, 0)
# Reloj simulado ALINEADO con la fecha que ve el script (NOW, UTC): la coherencia temporal por serie
# (hallazgo #7) compara la fecha máxima con el reloj del ejecutor.
NOW_EPOCH = (NOW - datetime(1970, 1, 1)).total_seconds()


def clock_at_now():
    return Clock(NOW_EPOCH)
_counter = [0]


def fixed_dt(now):
    class FixedDT(datetime):
        @classmethod
        def utcnow(cls):
            return cls(now.year, now.month, now.day, now.hour, now.minute, now.second)

        @classmethod
        def now(cls, tz=None):
            return cls(now.year, now.month, now.day, now.hour, now.minute, now.second, tzinfo=tz)
    return FixedDT


class _FakeReqMod(object):
    class RequestException(Exception):
        pass

    class HTTPError(RequestException):
        pass

    class Timeout(RequestException):
        pass

    class ConnectionError(RequestException):
        pass

    def __init__(self, serve):
        self.serve = serve
        self.exceptions = self
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        full = url + (("&" if "?" in url else "?") + urlencode(params) if params else "")
        self.calls.append(full)
        status, body = self.serve(full)
        mod = self

        class R(object):
            status_code = status
            content = body
            text = body.decode("utf-8", "replace")

            def json(self):
                return json.loads(self.text)

            def raise_for_status(self):
                if status >= 400:
                    raise mod.HTTPError("HTTP %s" % status)
        return R()


def _load(path, name):
    _counter[0] += 1
    spec = importlib.util.spec_from_file_location("%s_%d" % (name, _counter[0]), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_root(files):
    """Raíz temporal con data/<fichero> copiados del repo (o contenido dado) y sources/ reales."""
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "data"))
    os.makedirs(os.path.join(root, "scripts"))
    shutil.copytree(os.path.join(ROOT, "sources"), os.path.join(root, "sources"))
    for name, content in files.items():
        with open(os.path.join(root, "data", name), "wb") as fh:
            fh.write(content)
    return root


def run_original(script, serve, root, argv=(), now=NOW, output_attr="OUTPUT_PATH"):
    mod = _load(os.path.join(ROOT, "tests", "fixtures", "original", script + ".py"), script + "_orig")
    mod.requests = _FakeReqMod(serve)
    mod.datetime = fixed_dt(now)
    mod.__file__ = os.path.join(root, "scripts", script + ".py")
    from pathlib import Path
    if hasattr(mod, "OUTPUT_PATH"):
        mod.OUTPUT_PATH = Path(root) / "data" / Path(mod.OUTPUT_PATH).name
    if hasattr(mod, "OUTPUT_DIR"):
        mod.OUTPUT_DIR = Path(root) / "data"
    old = sys.argv
    sys.argv = [script + ".py"] + list(argv)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code
    finally:
        sys.argv = old
    return rc, mod


def run_new(script, serve, root, argv=(), now=NOW, clock=None):
    clock = clock or clock_at_now()
    calls = []

    def transport(method, url, headers, body, ct, rt, tt):
        calls.append(url)
        status, data = serve(url)
        if status == "RAISE":
            from g8common.g8http import NetError
            raise NetError("timeout", "simulado")
        hdrs = {}
        if isinstance(data, tuple):
            data, hdrs = data
        return status, hdrs, data
    ING.ROOT = root
    ING.TEST_TRANSPORT = transport
    ING.TEST_CLOCK = clock
    mod = _load(os.path.join(ROOT, "scripts", script + ".py"), script + "_new")
    mod.datetime = fixed_dt(now)
    old = sys.argv
    sys.argv = [script + ".py"] + list(argv)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code
    finally:
        sys.argv = old
        ING.TEST_TRANSPORT = None
        ING.TEST_CLOCK = None
    return rc, calls, clock


def read(root, name):
    p = os.path.join(root, "data", name)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as fh:
        return fh.read()


def repo_rows(name):
    """[(YYYYMMDD, texto_valor)] del CSV real del repo (columna CLOSE)."""
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as fh:
        rd = list(csv.reader(fh))
    hdr = rd[0]
    ci = hdr.index("CLOSE")
    return [(r[0], r[ci]) for r in rd[1:] if r and r[0].isdigit()]


def iso(d):
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:])


# ── envoltorios de cada fuente ────────────────────────────────────────────────
def ecb_csv(rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["KEY", "FREQ", "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS"])
    for d, v in rows:
        w.writerow(["EST.B.EU000A2X2A25.WT", "B", iso(d), v, "A"])
    return out.getvalue().encode()


def fred_csv(rows, series="SOFR"):
    return ("observation_date,%s\n" % series + "".join("%s,%s\n" % (iso(d), v) for d, v in rows)).encode()


def boe_csv(rows):
    return ("DATE,IUDSOIA\n" + "".join("%s,%s\n" % (datetime.strptime(d, "%Y%m%d").strftime("%d %b %Y"), v) for d, v in rows)).encode()


def boj_json(rows, status=200):
    return json.dumps({"STATUS": status, "RESULTSET": [{"SERIES_CODE": "STRDCLUCON", "VALUES": {
        "SURVEY_DATES": [int(d) for d, _ in rows], "VALUES": [float(v) for _, v in rows]}}]}).encode()


def valet_json(rows, code="AVG.INTWO"):
    return json.dumps({"observations": [{"d": iso(d), code: {"v": v}} for d, v in rows]}).encode()


def rba_f1(rows):
    head = ["F1 INTEREST RATES AND YIELDS – MONEY MARKET - Reserve Bank of Australia",
            "Title,Cash Rate Target,Interbank Overnight Cash Rate",
            "Frequency,Daily,Daily", "Series ID,FIRMMCRTD,FIRMMCRID"]
    body = ["%s,4.35,%s" % (datetime.strptime(d, "%Y%m%d").strftime("%d-%b-%Y"), v) for d, v in rows]
    return ("\n".join(head + body) + "\n").encode()


def bis_csv(rows):
    return ("FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n" +
            "".join("D,XX,%s,%s,A\n" % (iso(d), v) for d, v in rows)).encode()

"""frozen_data.py — datos del repo CONGELADOS para las pruebas con reloj fijo (integración F3, 25-sep).

Por qué: varias pruebas leían data/*.csv del árbol vivo y les añadían «un día nuevo» con fecha fija (p. ej. 2026-09-24)
bajo un reloj fijo (T_NOW = 24-sep). En cuanto producción publica ese día, el «día nuevo» ya existe y la prueba falla
sin que el código haya cambiado (14 fallos al fusionar con origin/main 6489853). Las pruebas no deben depender de los
datos que producción sigue escribiendo cada día.

Qué hace: sirve la copia exacta (byte a byte) de cada fichero tal como estaba en la base `ed9ed64`, guardada en
tests/fixtures/data_ed9ed64/ y comprobada con su SHA-256 (MANIFEST.sha256). Si falta un fichero o no coincide, la
prueba FALLA (nunca cae en silencio a los datos vivos).

Qué NO cambia: las pruebas que deben mirar el árbol vivo (coherencia del registro con data/, contratos de formato de
todos los ficheros actuales, cobertura de salidas de F3, «no se tocan los datos») siguen leyendo data/.
"""
import glob as _glob
import hashlib
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SNAPSHOT_REF = "ed9ed64"
DIR = os.path.join(ROOT, "tests", "fixtures", "data_" + SNAPSHOT_REF)
_MANIFEST = None


def _manifest():
    global _MANIFEST
    if _MANIFEST is None:
        m = {}
        with open(os.path.join(DIR, "MANIFEST.sha256"), encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    digest, name = line.split(None, 1)
                    m[name.strip()] = digest
        _MANIFEST = m
    return _MANIFEST


def path(name):
    """Ruta del fichero congelado `name` (p. ej. "TONA.csv"); verifica su SHA-256 contra el manifiesto."""
    m = _manifest()
    if name not in m:
        raise FileNotFoundError("frozen_data: %s no está en la copia congelada %s" % (name, SNAPSHOT_REF))
    p = os.path.join(DIR, name)
    with open(p, "rb") as fh:
        if hashlib.sha256(fh.read()).hexdigest() != m[name]:
            raise AssertionError("frozen_data: %s no coincide con su SHA-256 del manifiesto" % name)
    return p


def read_bytes(name):
    with open(path(name), "rb") as fh:
        return fh.read()


def glob(pattern):
    """Ficheros congelados que casan con `pattern` (solo nombre, sin carpeta), como rutas absolutas y ordenados."""
    names = {os.path.basename(p) for p in _glob.glob(os.path.join(DIR, pattern))}
    return [path(n) for n in sorted(names & set(_manifest()))]

"""Read-only evidence for operational health; never changes source/model data."""
import csv
import json
import math
from datetime import datetime, timedelta
from g8common.freshness import _parse_date


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def rate_rows(path, today):
    try:
        with path.open() as f:
            rows = csv.DictReader(line for line in f if line.strip() and not line.startswith('#'))
            out = []
            for row in rows:
                d = _parse_date(str(row.get('DATE') or row.get('Date') or ''))
                value = row.get('CLOSE', row.get('Value'))
                try:
                    v = float(value)
                except (TypeError, ValueError):
                    continue
                if d and math.isfinite(v) and d <= today:
                    out.append((d, v))
            return sorted(out)
    except OSError:
        return []


def refine(root, now, item, policy):
    name = item['file']
    # Absence is intentional but remains visible. Never relabel annual estimates monthly.
    if name == 'OFFICIAL_GOLD_DEMAND.csv':
        exists = (root / 'data' / name).exists()
        item.update(status='ERROR' if exists else 'EXCLUDED', state='PROHIBITED' if exists else 'RETIRED',
                    publication_frequency='excluded', slow_fallback=False,
                    reason='RETIRADO: archivo anual y estimado, no mensual. Sin sustituto mensual validado; oro usa su especificación baseline existente.')
        return
    if name.startswith('MFV_G8_walkforward_'):
        item.update(status='EXCLUDED', state='DIAGNOSTIC', publication_frequency='diagnostic',
                    reason='Diagnóstico de calibración; no es una serie de mercado ni prueba de frescura. No se le atribuye una fecha de observación.')
        return
    if name == 'NZD_CASH_ON.csv':
        item['reason'] = 'RBNZ B2 se publica diariamente, pero contiene casillas cash sin observación. No se rellenan; calendario exacto de observaciones aún sin certificar.'
    if name == 'OPTIONS_SURFACE.json':
        item['reason'] = 'Fecha de sesión visible; colector CME/Databento fuera del alcance de esta validación. No certificado como fresco por este monitor.'
    if name.startswith('MFV_G8_'):
        item['reason'] = 'Salida semanal derivada: falta certificar conjuntamente COT y precios externos. La fecha de ejecución no prueba frescura de todas las entradas.'
    if name == 'FLOOR_USD.csv':
        observations = rate_rows(root / 'data' / name, now.date())
        if observations:
            effective = observations[-1][0]
            announced = item.get('have_max')
            item['have_max'] = effective.isoformat()
            item['announced_max'] = announced
            expected = _parse_date(str(item.get('expected_obs') or ''))
            if expected and effective >= expected and item['status'] != 'ERROR':
                item.update(status='CURRENT', state='CURRENT',
                            reason='Última fecha efectiva vigente. Las fechas futuras publicadas por FRED se conservan como anuncios y no cuentan como observaciones actuales.')
            elif expected and effective < expected:
                item.update(status='LATE', reason='Última fecha efectiva anterior a la esperada; los anuncios futuros no cierran el retraso.')
        else:
            item.update(status='MISSING', reason='No hay una observación efectiva vigente; los anuncios futuros no cuentan.')
    evidence = policy.get(name)
    if not evidence or item['status'] == 'ERROR':
        return
    observations = rate_rows(root / 'data' / name, now.date())
    try:
        verified = datetime.fromisoformat(evidence['verified_at'].replace('Z', '+00:00'))
        expires = min(datetime.fromisoformat(evidence['expires_at'].replace('Z', '+00:00')),
                      datetime.fromisoformat(evidence['next_review_at'].replace('Z', '+00:00')),
                      verified + timedelta(days=7))
        rate = float(evidence['rate'])
        effective = _parse_date(evidence['effective_on_or_before'])
        if not effective or not math.isfinite(rate) or not evidence['source'].startswith('https://'):
            raise ValueError('invalid evidence')
        item.update(evidence_source=evidence['source'], evidence_expires_utc=expires.isoformat(),
                    next_decision=evidence['next_review_at'])
        if now < verified or now >= expires:
            item.update(status='UNKNOWN', reason='Verificación oficial caducada o futura: requiere nueva consulta de decisión y calendario. No basta repetir el último tipo.')
        elif not observations or observations[-1][0] < effective:
            item.update(status='LATE', reason='Serie anterior a la decisión oficial verificada o sin observación vigente.')
        elif not math.isclose(observations[-1][1], rate, abs_tol=1e-8, rel_tol=0):
            item.update(status='ERROR', reason='Tipo publicado distinto de la decisión oficial verificada; revisar fuente antes de certificar.')
        elif item['status'] != 'LATE':
            item.update(status='CURRENT', state='VERIFIED_POLICY', have_max=observations[-1][0].isoformat(),
                        reason='Tipo vigente contrastado con decisión oficial; verificación limitada a siete días y a la próxima reunión. No es una nueva observación diaria.')
    except (KeyError, ValueError, TypeError):
        item.update(status='UNKNOWN', reason='Evidencia de política incompleta o inválida; no se certifica vigencia.')

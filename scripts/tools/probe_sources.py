#!/usr/bin/env python3
"""Manual public-source diagnostics; writes artifacts only, never published data."""
import json
from pathlib import Path
import requests

URLS = {
    'boe_latest.zip': 'https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip',
    'bis_api.csv': 'https://stats.bis.org/api/v1/data/WS_CBPOL/D.JP/all?format=csv',
    'bis_bulk.zip': 'https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip',
}


def main():
    out = Path('source-probes')
    out.mkdir(exist_ok=True)
    result = {}
    for name, url in URLS.items():
        try:
            r = requests.get(url, timeout=(10, 90), headers={'User-Agent': 'g8-macro-pipeline/1.0'})
            result[name] = {'url': url, 'status': r.status_code, 'bytes': len(r.content), 'content_type': r.headers.get('Content-Type')}
            if r.ok:
                (out / name).write_bytes(r.content)
                if not name.endswith('.zip'):
                    result[name]['preview'] = r.text[:1500]
        except requests.RequestException as e:
            result[name] = {'url': url, 'error': str(e)}
    (out / 'report.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

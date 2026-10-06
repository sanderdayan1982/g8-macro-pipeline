"""Input observation provenance and all-or-nothing publication for weekly metals.
Operational controls only: no Kalman, calibration, signal or resampling changes.
"""
import hashlib
import json
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from g8common.series import write_atomic

OUTPUTS = ['MFV_G8_XAU.csv', 'MFV_G8_XAG.csv', 'MFV_G8_state.json',
           'MFV_G8_walkforward_XAU.csv', 'MFV_G8_walkforward_XAG.csv']
FREQUENCIES = {'DGS10':'daily','EFFR':'daily','NYFED_EFFR':'daily','T10YIE':'daily',   # acta P-9: FEDFUNDS (mensual) retirado
               'DFII10':'daily','DTWEXBGS':'weekly','WRESBAL':'weekly','WTREGEN':'weekly'}


class Evidence:
    def __init__(self):
        self.inputs = {}
        self.metal = None
        self.anchor = None

    def record(self, key, series, source, frequency):
        if self.metal is None:
            return series
        dates = sorted({x.date() for x in series.dropna().index if str(x) != 'NaT'})
        eligible = [d for d in dates if d <= self.anchor]
        self.inputs.setdefault(self.metal, {})[key] = {
            'source': source, 'publication_frequency': frequency,
            'latest_observation': dates[-1].isoformat() if dates else None,
            'used_observation': eligible[-1].isoformat() if eligible else None}
        return series

    def validate(self, metal):
        inputs = self.inputs.get(metal, {})
        required = {'price:XAU','BE10','DGS10','NFA_DEBT','WRESBAL','WTREGEN','DTWEXBGS','REAL10'} if metal == 'XAU' else {'price:XAG','price:XAU','REAL10','DTWEXBGS'}
        # FRED fallbacks keep their own identifiers, never hide the source used.
        present = set(inputs)
        if 'T10YIE' in present: present.add('BE10')
        if 'DFII10' in present: present.add('REAL10')
        if metal == 'XAU' and not ({'EFFR','NYFED_EFFR'} & present):
            raise ValueError('Missing effective-rate evidence')
        if required - present:
            raise ValueError('Missing input evidence: ' + ','.join(sorted(required-present)))
        for key, item in inputs.items():
            freq = item['publication_frequency']
            if freq not in ('daily','weekly','monthly'):
                raise ValueError('Prohibited or unverified frequency: ' + key)
            used = date.fromisoformat(item['used_observation']) if item['used_observation'] else None
            # Weekly model: original observation must be inside its native period.
            # This is an operational publication guard, not a model parameter.
            limit = 31 if freq == 'monthly' else 7
            if not used or not 0 <= (self.anchor-used).days <= limit:
                raise ValueError('Input outside publication period at weekly anchor: ' + key)
        return inputs


def snapshot(data):
    return {name:(data/name).read_bytes() if (data/name).exists() else None for name in OUTPUTS}


def restore(data, before):
    for name, content in before.items():
        p=data/name
        if content is None:
            p.unlink(missing_ok=True)
        else:
            write_atomic(str(p),content)


def manifest(data, evidence, targets, error=None):
    result={'schema':'G8_METALS_INPUTS/1','generated_utc':datetime.now(timezone.utc).isoformat(),
            'anchor':evidence.anchor.isoformat(),'status':'ERROR' if error else 'VERIFIED',
            'error':error,'inputs':evidence.inputs,'outputs':{}}
    if not error:
        for name in targets:
            evidence.validate(name)
        result['outputs']={n:hashlib.sha256((data/n).read_bytes()).hexdigest() for n in OUTPUTS if (data/n).exists()}
    write_atomic(str(data/'_ingest/metals_provenance.json'),(json.dumps(result,indent=2)+'\n').encode())
    return result

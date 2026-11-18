"""Ningún workflow de este repo público sube un artefacto CME.

orden Sander 18-nov t244u.
"""
import os
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WF_DIR = os.path.join(ROOT, ".github", "workflows")


def _blob(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_blob(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(_blob(item) for item in value.values())
    return "" if value is None else str(value)


class NoCmePublicArtifact(unittest.TestCase):
    def test_no_workflow_uploads_an_artifact_named_or_pathed_cme(self):
        # orden Sander 18-nov t244u
        for name in sorted(os.listdir(WF_DIR)):
            if not name.endswith((".yml", ".yaml")):
                continue
            with open(os.path.join(WF_DIR, name), encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
            jobs = doc.get("jobs") or {}
            for job in jobs.values():
                if not isinstance(job, dict):
                    continue
                for step in job.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    if "upload-artifact" not in str(step.get("uses") or ""):
                        continue
                    spec = step.get("with") or {}
                    blob = _blob(spec.get("name")) + "\n" + _blob(spec.get("path"))
                    self.assertNotIn("cme", blob.lower(), name)


if __name__ == "__main__":
    unittest.main()

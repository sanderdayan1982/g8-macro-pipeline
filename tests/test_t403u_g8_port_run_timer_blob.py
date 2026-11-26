"""t403u: la copia vps/g8-port-run.timer es el blob de g8-macro-data #81 (head 44dce435)."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Blob de infra/vps/systemd/g8-port-run.timer en g8-macro-data #81, head 44dce435.
BLOB_G8_MACRO_DATA_81 = "cfa0a8c132f494dee8eb6570a3be392d50416895"


class PortRunTimerBlob(unittest.TestCase):
    def test_vps_copy_blob_matches_g8_macro_data_pr81(self):
        path = ROOT / "vps" / "g8-port-run.timer"
        blob = subprocess.check_output(
            ["git", "hash-object", str(path)],
            cwd=str(ROOT),
        ).decode().strip()
        self.assertEqual(blob, BLOB_G8_MACRO_DATA_81)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Compare una corrida dry-run del VPS con un commit público de Actions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from daily_update_vps import DRYRUN_SCHEMA, publishable


def timing(path):
    name = PurePosixPath(path).name.lower()
    return path.startswith("data/_ingest/") or any(
        word in name for word in ("watch_state", "health", "steps", "ledger")
    )


def compare(manifest, repo, actions_sha):
    doc = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if doc["schema"] != DRYRUN_SCHEMA:
        raise ValueError("unsupported manifest schema")
    files = {}
    for entry in doc["files"]:
        path, digest = entry["path"], entry["sha256"]
        if (not publishable(path) or any(part in ("", ".", "..") for part in path.split("/"))
                or "\0" in path or path in files or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("invalid manifest file entry")
        files[path] = digest
    if not re.fullmatch(r"[0-9a-fA-F]{4,64}", actions_sha):
        raise ValueError("expected an Actions commit SHA")

    env = {"PATH": os.environ.get("PATH", os.defpath),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_LAZY_FETCH": "1"}

    def git(*args, check=True):
        return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, check=check)

    sha = git("rev-parse", "--verify", actions_sha + "^{commit}").stdout.decode().strip()
    changed = git("show", "--format=", "--name-only", "-z", "--no-renames", "--root",
                  "--diff-merges=first-parent", sha, "--").stdout
    actions_paths = {path for path in map(os.fsdecode, changed.split(b"\0")) if publishable(path)}

    groups = {name: [] for name in ("SAME", "DIFF", "ONLY_VPS", "ONLY_ACTIONS", "TIMING")}
    for path in sorted(files.keys() | actions_paths):
        if timing(path):
            group = "TIMING"
        elif path not in files:
            group = "ONLY_ACTIONS"
        else:
            blob = git("show", sha + ":" + path, check=False)
            if blob.returncode:
                group = "ONLY_VPS"
            elif hashlib.sha256(blob.stdout).hexdigest() == files[path]:
                group = "SAME"
            else:
                group = "DIFF"
        groups[group].append(path)
    return groups


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--actions-sha", required=True)
    args = parser.parse_args(argv)
    try:
        groups = compare(args.manifest, args.repo, args.actions_sha)
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        print("compare_daily_dryrun: invalid manifest, repository, or Actions SHA", file=sys.stderr)
        return 1
    for name, paths in groups.items():
        print(f"{name} {len(paths)}")
        for path in paths:
            print(f"  {path}")
    return int(any(groups[name] for name in ("DIFF", "ONLY_VPS", "ONLY_ACTIONS")))


if __name__ == "__main__":
    sys.exit(main())

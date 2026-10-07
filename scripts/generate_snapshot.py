#!/usr/bin/env python3
"""Generate and verify one snapshot without publishing it."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import export_codebase

VERSION = "1.1.0"

class GenerationError(RuntimeError):
    pass

def git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(root), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if completed.returncode:
        raise GenerationError("GIT_PRECONDITION_FAILED")
    return completed.stdout.strip()

def provenance(root: Path) -> dict[str, object]:
    canonical = root.resolve(strict=True)
    if Path(git(canonical, "rev-parse", "--show-toplevel")).resolve(strict=True) != canonical:
        raise GenerationError("GIT_ROOT_MISMATCH")
    branch = git(canonical, "branch", "--show-current")
    head = git(canonical, "rev-parse", "HEAD")
    remote = git(canonical, "rev-parse", "origin/main")
    status = git(canonical, "status", "--porcelain=v1", "--untracked-files=all")
    if branch != "main": raise GenerationError("AUTHORITATIVE_BRANCH_REQUIRED")
    if status: raise GenerationError("SNAPSHOT_SOURCE_DIRTY")
    if head != remote: raise GenerationError("SNAPSHOT_SOURCE_NOT_SYNCHRONIZED")
    tracked = git(canonical, "ls-files").splitlines()
    return {"gitRoot": str(canonical), "branch": branch, "gitCommit": head,
            "originMain": remote, "worktreeClean": True, "trackedFileCount": len(tracked)}

def generate(root_arg: str, output_arg: str, result_arg: str) -> dict[str, object]:
    root = Path(root_arg).resolve(strict=True)
    output = Path(output_arg).resolve(strict=False)
    result = Path(result_arg).resolve(strict=False)
    if output == result or output.parent != result.parent:
        raise GenerationError("OUTPUT_PATHS_INVALID")
    output.parent.mkdir(parents=True, exist_ok=True)
    before = provenance(root)
    message = export_codebase.export(str(root), str(output), managed_publication=True)
    ok, verification = export_codebase.verify(str(root), str(output))
    if not ok: raise GenerationError("INDEPENDENT_VERIFICATION_FAILED")
    after = provenance(root)
    if before != after: raise GenerationError("SNAPSHOT_SOURCE_CHANGED")
    provenance_json = json.dumps(before, sort_keys=True, separators=(",", ":")).encode()
    with output.open("a", encoding="utf-8") as handle:
        handle.write("\n<!-- CODEBASE_MD_EXPORT_GIT " +
                     base64.b64encode(provenance_json).decode() + " -->\n")
    ok, verification = export_codebase.verify(str(root), str(output))
    if not ok: raise GenerationError("FINAL_ARTIFACT_VERIFICATION_FAILED")
    payload = output.read_bytes()
    metadata, manifest, _ = export_codebase.parse_artifact(payload.decode("utf-8"))
    dispositions = [entry["disposition"] for entry in manifest]
    data = {"status": "PASS", "skillVersion": VERSION,
        "exporterVersion": export_codebase.VERSION,
        "canonicalSnapshotDigest": metadata["digest"],
        "discoveredCount": len(manifest), "classifiedCount": len(manifest),
        "includedCount": sum(v.startswith("INCLUDED") for v in dispositions),
        "redactedCount": sum(int(e.get("redactions", 0)) for e in manifest),
        "omittedCount": sum(not v.startswith("INCLUDED") and v != "DIRECTORY" for v in dispositions),
        "errorCount": sum(v.startswith("ERROR") for v in dispositions),
        "sourceGitCommit": before["gitCommit"], "sourceOriginMain": before["originMain"],
        "trackedFileCount": before["trackedFileCount"], "outputSizeBytes": len(payload),
        "outputSha256": hashlib.sha256(payload).hexdigest(),
        "verification": verification, "generation": message}
    result.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return data

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--mode", choices=["strict-complete"], default="strict-complete")
    args = parser.parse_args()
    try:
        data = generate(args.root, args.output, args.result_json)
        print(json.dumps({"status": data["status"], "outputSizeBytes": data["outputSizeBytes"]}))
        return 0
    except Exception as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1

if __name__ == "__main__": raise SystemExit(main())

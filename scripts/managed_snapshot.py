#!/usr/bin/env python3
"""Publish a verified codebase snapshot through the local Document Pipeline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

PROJECT_RE = __import__("re").compile(r"[a-z0-9][a-z0-9-]{0,99}\Z")
REPOSITORIES_ROOT = Path("/srv/repos")
DOCUMENTS_ROOT = Path("/srv/documents")
DEFAULT_KEY_FILE = Path("/srv/secrets/mcp-documents/rest-api-key.txt")


class ManagedSnapshotError(RuntimeError):
    pass


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise ManagedSnapshotError("GIT_PRECONDITION_FAILED: " + result.stderr.strip())
    return result.stdout.strip()


def infer_project_id(root: Path, explicit: str | None) -> str:
    canonical = root.resolve(strict=True)
    repos = REPOSITORIES_ROOT.resolve(strict=True)
    if explicit:
        project_id = explicit
    else:
        if canonical.parent != repos:
            raise ManagedSnapshotError("PROJECT_ID_REQUIRED: root is not a direct child of /srv/repos")
        project_id = canonical.name
    if not PROJECT_RE.fullmatch(project_id):
        raise ManagedSnapshotError("INVALID_PROJECT_ID")
    return project_id


def git_provenance(root: Path) -> dict[str, str]:
    top = Path(run_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root.resolve(strict=True):
        raise ManagedSnapshotError("GIT_ROOT_MISMATCH")
    branch = run_git(root, "branch", "--show-current")
    head = run_git(root, "rev-parse", "HEAD")
    origin = run_git(root, "rev-parse", "origin/main")
    status = run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    tracked = run_git(root, "ls-files")
    if branch != "main":
        raise ManagedSnapshotError("AUTHORITATIVE_BRANCH_REQUIRED: main")
    if status:
        raise ManagedSnapshotError("DIRTY_MANAGED_REPOSITORY")
    if head != origin:
        raise ManagedSnapshotError("LOCAL_MAIN_DIVERGES_FROM_ORIGIN_MAIN")
    return {"gitRoot": str(top), "branch": branch, "gitCommit": head,
            "originMain": origin, "worktree": "clean",
            "trackedFileCount": str(len(tracked.splitlines()) if tracked else 0)}


class Pipeline:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.key = key.strip()

    def request(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-API-Key": self.key,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise ManagedSnapshotError(f"PIPELINE_HTTP_{error.code}: {detail}") from error
        except OSError as error:
            raise ManagedSnapshotError("PUBLICATION_UNAVAILABLE: " + str(error)) from error


def ensure_registered(pipeline: Pipeline, project_id: str) -> None:
    projects = pipeline.request("GET", "/api/v1/projects")
    match = next((p for p in projects if p.get("projectId") == project_id), None)
    if not match or "code-snapshot" not in match.get("documentTypes", []):
        raise ManagedSnapshotError("PROJECT_NOT_REGISTERED")
    expected = DOCUMENTS_ROOT / project_id
    if not expected.is_dir() or expected.is_symlink():
        raise ManagedSnapshotError("PROJECT_DOCUMENT_ROOT_INVALID")


def snapshot(root_arg: str, project_id: str | None, subject: str, api_url: str,
             api_key_file: Path, exporter: Path) -> dict:
    root = Path(root_arg).resolve(strict=True)
    pid = infer_project_id(root, project_id)
    before = git_provenance(root)
    pipeline = Pipeline(api_url, api_key_file.read_text(encoding="utf-8"))
    ensure_registered(pipeline, pid)
    with tempfile.TemporaryDirectory(prefix="codebase-snapshot-") as directory:
        artifact = Path(directory) / "snapshot.md"
        command = [sys.executable, str(exporter), "export", str(root), "--output",
                   str(artifact), "--mode", "strict-complete", "--verify",
                   "--managed-publication"]
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        if completed.returncode:
            raise ManagedSnapshotError("EXPORT_FAILED: " + completed.stderr.strip())
        verified = subprocess.run([sys.executable, str(exporter), "verify", str(root),
                                   "--output", str(artifact)], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if verified.returncode:
            raise ManagedSnapshotError("INDEPENDENT_VERIFICATION_FAILED: " + verified.stdout.strip())
        with artifact.open("a", encoding="utf-8") as handle:
            handle.write("\n## Managed Git Provenance\n\n```json\n")
            handle.write(json.dumps(before, ensure_ascii=False, sort_keys=True, indent=2))
            handle.write("\n```\n")
        post_verify = subprocess.run([sys.executable, str(exporter), "verify", str(root),
                                      "--output", str(artifact)], text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if post_verify.returncode:
            raise ManagedSnapshotError("FINAL_ARTIFACT_VERIFICATION_FAILED: " + post_verify.stdout.strip())
        payload = artifact.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        context = pipeline.request("POST", f"/api/v1/projects/{pid}/generation-contexts", {
            "operationId": "snapshot-context-" + uuid.uuid4().hex,
            "sourceType": "CODEX", "subject": subject})
        receipt = pipeline.request("POST", f"/api/v1/projects/{pid}/documents:publish", {
            "operationId": "snapshot-publish-" + uuid.uuid4().hex,
            "generationContextId": context["generationContextId"],
            "documents": [{"documentType": "code-snapshot", "subject": subject,
                           "expectedSizeBytes": len(payload), "expectedSha256": sha,
                           "content": payload.decode("utf-8")} ]})
        after = git_provenance(root)
        if before != after:
            raise ManagedSnapshotError("TARGET_REPOSITORY_CHANGED_DURING_RUN")
        document = receipt["documents"][0]
        required = (receipt.get("status") == "COMPLETED" and
                    document.get("status") == "PERSISTED_AND_VERIFIED" and
                    document.get("sourceIntegrityStatus") == "VERIFIED" and
                    document.get("storedIntegrityStatus") == "VERIFIED" and
                    document.get("sourceSha256") == sha and document.get("storedSha256") == sha)
        if not required:
            raise ManagedSnapshotError("PUBLICATION_INTEGRITY_FAILED")
        return {"projectId": pid, "documentType": "code-snapshot", "subject": subject,
                "snapshotDigest": sha, "sizeBytes": len(payload), "git": before,
                "verification": post_verify.stdout.strip(), "publication": receipt}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root_dir")
    parser.add_argument("--project-id")
    parser.add_argument("--subject", default="codebase-snapshot")
    parser.add_argument("--api-url", default=os.environ.get("DOCUMENT_PIPELINE_URL", "http://127.0.0.1:8088"))
    parser.add_argument("--api-key-file", type=Path, default=DEFAULT_KEY_FILE)
    parser.add_argument("--exporter", type=Path, default=Path(__file__).with_name("export_codebase.py"))
    args = parser.parse_args()
    try:
        print(json.dumps(snapshot(args.root_dir, args.project_id, args.subject, args.api_url,
                                  args.api_key_file, args.exporter), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

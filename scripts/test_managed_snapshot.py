#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE_PATH = Path(__file__).with_name("managed_snapshot.py")
SPEC = importlib.util.spec_from_file_location("managed_snapshot", MODULE_PATH)
managed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(managed)


class ManagedSnapshotTests(unittest.TestCase):
    def test_routes_direct_managed_projects_generically(self):
        for project in ("rust", "mcp-documents", "orquestador"):
            with self.subTest(project=project):
                self.assertEqual(managed.infer_project_id(Path("/srv/repos") / project, None), project)

    def test_arbitrary_path_requires_explicit_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(managed.ManagedSnapshotError, "PROJECT_ID_REQUIRED"):
                managed.infer_project_id(Path(directory), None)
            self.assertEqual(managed.infer_project_id(Path(directory), "synthetic-project"),
                             "synthetic-project")

    def test_invalid_project_identifier_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(managed.ManagedSnapshotError, "INVALID_PROJECT_ID"):
                managed.infer_project_id(Path(directory), "../escape")

    def test_dirty_and_diverged_repositories_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            (root / "tracked").write_text("one\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-m", "one"], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main",
                            subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()], check=True)
            self.assertEqual(managed.git_provenance(root)["worktree"], "clean")
            (root / "untracked").write_text("dirty\n")
            with self.assertRaisesRegex(managed.ManagedSnapshotError, "DIRTY_MANAGED_REPOSITORY"):
                managed.git_provenance(root)
            (root / "untracked").unlink()
            (root / "tracked").write_text("two\n")
            subprocess.run(["git", "-C", str(root), "commit", "-am", "two"], check=True,
                           stdout=subprocess.DEVNULL)
            with self.assertRaisesRegex(managed.ManagedSnapshotError, "LOCAL_MAIN_DIVERGES"):
                managed.git_provenance(root)

    def test_unknown_project_is_explicit_failure(self):
        class FakePipeline:
            def request(self, method, path, body=None):
                return [{"projectId": "known", "documentTypes": ["code-snapshot"]}]
        with self.assertRaisesRegex(managed.ManagedSnapshotError, "PROJECT_NOT_REGISTERED"):
            managed.ensure_registered(FakePipeline(), "unknown")

    def test_publication_unavailable_is_explicit_failure(self):
        pipeline = managed.Pipeline("http://127.0.0.1:1", "synthetic-key")
        with self.assertRaisesRegex(managed.ManagedSnapshotError, "PUBLICATION_UNAVAILABLE"):
            pipeline.request("GET", "/api/v1/projects")


if __name__ == "__main__":
    unittest.main()

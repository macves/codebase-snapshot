import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("generate_snapshot.py")


class GenerateSnapshotTest(unittest.TestCase):
    def test_generation_only_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            (root / "Cargo.toml").write_text("[package]\nname='demo'\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "Cargo.toml"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-m", "initial"], check=True,
                           stdout=subprocess.DEVNULL)
            head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", head], check=True)
            output = Path(directory) / "private" / "snapshot.md"
            result = output.with_name("result.json")
            completed = subprocess.run([str(SCRIPT), "--root", str(root), "--output", str(output),
                "--result-json", str(result), "--mode", "strict-complete"], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(0, completed.returncode, completed.stderr)
            metadata = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual("PASS", metadata["status"])
            self.assertEqual(head, metadata["sourceGitCommit"])
            self.assertEqual(output.stat().st_size, metadata["outputSizeBytes"])
            self.assertIn("Cargo.toml", output.read_text(encoding="utf-8"))

    def test_dirty_source_fails_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(root)], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            (root / "source.txt").write_text("clean\n")
            subprocess.run(["git", "-C", str(root), "add", "source.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-m", "initial"], check=True,
                           stdout=subprocess.DEVNULL)
            head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", head], check=True)
            (root / "source.txt").write_text("dirty\n")
            output = Path(directory) / "snapshot.md"
            completed = subprocess.run([str(SCRIPT), "--root", str(root), "--output", str(output),
                "--result-json", str(Path(directory) / "result.json")], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("SNAPSHOT_SOURCE_DIRTY", completed.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

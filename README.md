# codebase-snapshot

Canonical source for the `codebase-md-export` Codex Skill. It creates fresh,
complete, independently verified and redacted Markdown snapshots.

For a registered repository under `/srv/repos/<projectId>`, use:

```bash
scripts/managed_snapshot.py /srv/repos/<projectId> \
  --api-url http://127.0.0.1:8088
```

The project ID is inferred only for a direct child of `/srv/repos`. The tool
requires clean synchronized `main`, generates outside the source tree, verifies
the export, and publishes exact bytes as `code-snapshot` through the Document
Pipeline. For a directory outside `/srv/repos`, pass `--project-id`; it must
still be registered. Explicit file export remains available only for
non-managed roots through `scripts/export_codebase.py`.

Server-side integrations use the non-publishing machine interface:

```bash
scripts/generate_snapshot.py --root /srv/repos/<projectId> \
  --output /private/temp/snapshot.md \
  --result-json /private/temp/result.json --mode strict-complete
```

It performs no network calls and never publishes. It requires a clean `main`
synchronized with `origin/main`, independently verifies the generated artifact,
rechecks Git provenance, and writes machine-readable result metadata.

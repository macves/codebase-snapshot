# Format and safety contract

`export_codebase.py` emits a human-readable Markdown document with metadata, directory tree, manifest, redaction summary, source sections, exclusions, and verification. Machine-readable manifest and source markers are base64 JSON HTML comments so the verifier can cross-check the displayed artifact without relying on a previous snapshot.

The canonical snapshot digest derives from the normalized classified inventory and the redacted emitted text. It intentionally excludes timestamps and destination-specific temporary names. Thus unchanged inputs produce the same digest and source ordering.

Text is strict UTF-8 and NUL-free. A file with a decode/read error prevents a successful strict-complete export; binary data is manifested but not embedded. The root-level `target/` build-output directory is deliberately excluded and recorded as `OMITTED_TARGET_BUILD`. Symlinks are classified rather than followed, so links cannot escape the canonical root or form traversal cycles.

Redaction uses deterministic placeholders (`<REDACTED_SECRET>`). It redacts strong credential-bearing assignments, bearer values, credential-bearing URL user-info, and known token-shaped values. Private-key payload files are not embedded. The detector deliberately does not treat policy keys such as `password_min_length` as credentials. New detector changes must add synthetic canaries to `selftest` and must never print their values.

The source tree is read only. Before and after each capture the file stat tuple is compared; the complete capture retries a bounded number of times and fails if a stable snapshot cannot be made. The output is first verified as a temporary file in the final destination directory, then replaced with `os.replace`.

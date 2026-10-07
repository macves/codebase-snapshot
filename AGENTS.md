# Project policy

- Canonical source: `/srv/repos/codebase-snapshot` on clean, synchronized `main`.
- Installed Skill: `$HOME/.codex/skills/codebase-md-export`, synchronized from a canonical commit.
- Skill documents: `/srv/documents/codebase-snapshot`.
- Managed snapshots: `/srv/documents/<projectId>/code-snapshot` through the registered Document Pipeline.
- Never store generated snapshots in source repositories or commit private project snapshots here.
- Prefer local filesystem reads to MCP when managed documents are locally available.
- Finish every change with clean `main == origin/main`.


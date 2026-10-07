#!/usr/bin/env python3
"""Fresh, deterministic, redacted codebase-to-Markdown exporter for Debian."""
from __future__ import annotations

import argparse, base64, codecs, hashlib, json, os, re, sys, tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

VERSION = "1.0.0"
REDACTED = "<REDACTED_SECRET>"
SECRET = r"(?:password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|access[_-]?key|secret[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|bearer[_-]?token|private[_-]?key|credential(?:s)?)"
# Assignment recognition is deliberately bounded to conventional configuration
# keys.  Scanning arbitrary long Markdown/base64 lines with a permissive
# backtracking expression can otherwise turn a strict-complete export into a
# CPU-bound operation.
ASSIGNMENT = re.compile(r"^(?P<prefix>\s*[\"']?(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)[\"']?\s*(?:=|:)\s*)")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}")
URI_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)([^\s/@:]+):([^\s/@]+)@")
TOKEN = re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|AIza[\w-]{20,}|eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,})\b")
PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")
VCS_NAMES = {".git", ".hg", ".svn", ".bzr"}
LANG = {".java":"java", ".rs":"rust", ".py":"python", ".js":"javascript", ".ts":"typescript", ".json":"json", ".yml":"yaml", ".yaml":"yaml", ".toml":"toml", ".xml":"xml", ".properties":"properties", ".ini":"ini", ".sql":"sql", ".sh":"shell", ".md":"markdown", ".html":"html", ".css":"css"}

@dataclass
class Record:
    path: str; kind: str; disposition: str; size: int = 0; lines: int | None = None
    language: str = "text"; fingerprint: str | None = None; redactions: int = 0; note: str = ""; content: str | None = None

def b64_json(v): return base64.b64encode(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).decode()
def unb64_json(v): return json.loads(base64.b64decode(v.encode()).decode())
def rel(root, p): return p.relative_to(root).as_posix()
def stat_key(p):
    s = p.stat(); return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
def lang(p): return LANG.get(Path(p).suffix.lower(), "text")
def fence(text): return "~" * max(3, max((len(x) for x in re.findall(r"~+", text)), default=0) + 1)

def secret_key(key):
    k = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    safe_suffixes = ("_file", "_path", "_name", "_enabled", "_policy",
                     "_min_length", "_rotation_days")
    if k.endswith(safe_suffixes): return False
    return bool(re.search(r"(?:^|_)" + SECRET + r"(?:$|_)", k, re.I))

def redact(text):
    count = 0
    safe_lines = []
    for line in text.splitlines(keepends=True):
        ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        body = line[:-len(ending)] if ending else line
        m = ASSIGNMENT.match(body)
        if not m or not secret_key(m.group("key")):
            safe_lines.append(line); continue
        value = body[m.end():]
        if not value.strip():
            safe_lines.append(line); continue
        # Preserve an inline comment while replacing only the assigned value.
        content, marker, comment = value.partition("#")
        quote = content.lstrip()[:1]
        replacement = quote + REDACTED + quote if quote in {"\"", "'"} else REDACTED
        spacing = content[:len(content) - len(content.lstrip())]
        safe_lines.append(m.group("prefix") + spacing + replacement + (marker + comment if marker else "") + ending)
        count += 1
    text = "".join(safe_lines)
    text, n = BEARER.subn("Bearer " + REDACTED, text); count += n
    text, n = URI_USERINFO.subn(r"\1" + REDACTED + ":" + REDACTED + "@", text); count += n
    text, n = TOKEN.subn(REDACTED, text); count += n
    return text, count

def read_text(p):
    before = stat_key(p); decoder = codecs.getincrementaldecoder("utf-8")("strict"); parts = []
    with p.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            if b"\0" in chunk: raise ValueError("binary NUL byte")
            parts.append(decoder.decode(chunk))
        parts.append(decoder.decode(b"", final=True))
    if before != stat_key(p): raise RuntimeError("mutated during capture")
    text = "".join(parts); return text, text.count("\n") + (1 if text else 0)

def scan(root_arg, output_arg, temp=None, extra_excluded=()):
    root = Path(root_arg).resolve(strict=True)
    if not root.is_dir(): raise ValueError("root_dir must be a readable directory")
    output = Path(output_arg).resolve(strict=False); temporary = Path(temp).resolve(strict=False) if temp else None
    extras = {str(Path(x).resolve(strict=False)) for x in extra_excluded}
    output_seen = False
    records = []
    def visit(directory):
        nonlocal output_seen
        for ent in sorted(os.scandir(directory), key=lambda e: os.fsencode(e.name)):
            p = Path(ent.path); rp = rel(root, p)
            try:
                if ent.is_symlink():
                    try: p.resolve(strict=False).relative_to(root); disp, note = "OMITTED_INTERNAL_SYMLINK", "not dereferenced"
                    except ValueError: disp, note = "OMITTED_EXTERNAL_SYMLINK", "target outside canonical root"
                    records.append(Record(rp, "symlink", disp, note=note)); continue
                if ent.is_dir(follow_symlinks=False):
                    if ent.name in VCS_NAMES: records.append(Record(rp, "directory", "OMITTED_VCS_INTERNAL", note="VCS internals")); continue
                    if directory == root and ent.name == "target":
                        records.append(Record(rp, "directory", "OMITTED_TARGET_BUILD", note="root-level project build output")); continue
                    records.append(Record(rp, "directory", "DIRECTORY")); visit(p); continue
                if not ent.is_file(follow_symlinks=False): records.append(Record(rp, "other", "OMITTED_SPECIAL", note="not a regular file")); continue
                canonical = p.resolve(strict=False)
                if temporary and canonical == temporary: continue
                if canonical == output:
                    records.append(Record(rp, "file", "OMITTED_OUTPUT_SELF", note="final output artifact")); output_seen = True; continue
                if str(canonical) in extras: continue
                try: text, lines = read_text(p)
                except UnicodeDecodeError as x: records.append(Record(rp, "file", "ERROR_DECODE", ent.stat(follow_symlinks=False).st_size, note=x.reason)); continue
                except ValueError as x: records.append(Record(rp, "file", "OMITTED_BINARY", ent.stat(follow_symlinks=False).st_size, note=str(x))); continue
                except (OSError, RuntimeError) as x: records.append(Record(rp, "file", "ERROR_UNREADABLE" if isinstance(x, OSError) else "ERROR_MUTATED_DURING_SNAPSHOT", note=str(x))); continue
                if PRIVATE_KEY.search(text) or (p.name.lower() in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"} and text):
                    records.append(Record(rp, "file", "OMITTED_SECRET_ONLY", len(text.encode()), lines, lang(rp), note="private-key or credential payload")); continue
                safe, n = redact(text); disp = "INCLUDED_REDACTED_TEXT" if n else "INCLUDED_TEXT"
                records.append(Record(rp, "file", disp, len(text.encode()), lines, lang(rp), hashlib.sha256(safe.encode()).hexdigest(), n, content=safe))
            except OSError as x: records.append(Record(rp, "unknown", "ERROR_UNREADABLE", note=str(x)))
    visit(root)
    try:
        output_relative = rel(root, output)
        if not output_seen: records.append(Record(output_relative, "file", "OMITTED_OUTPUT_SELF", note="final output artifact"))
    except ValueError: pass
    records.sort(key=lambda r: r.path); return root, records

def digest(records):
    payload = [{k:v for k,v in asdict(r).items() if k != "content"} for r in records]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def render(root, records):
    d = digest(records); inc = [r for r in records if r.disposition.startswith("INCLUDED")]; errors = [r for r in records if r.disposition.startswith("ERROR")]
    meta = {"skill":"codebase-md-export", "version":VERSION, "mode":"strict-complete", "root":str(root), "digest":d, "discovered":len(records), "included":len(inc), "errors":len(errors), "redactions":sum(r.redactions for r in records)}
    manifest = [{k:v for k,v in asdict(r).items() if k != "content"} for r in records]
    out = ["# Codebase Snapshot", "", "## Snapshot Metadata", "", "- Skill/version: codebase-md-export/" + VERSION, "- Mode: strict-complete", "- Safe root identity: `" + str(root) + "`", "- Canonical snapshot digest: `" + d + "`", f"- Counts: discovered={len(records)}, included={len(inc)}, errors={len(errors)}", "", "<!-- CODEBASE_MD_EXPORT_METADATA " + b64_json(meta) + " -->", "", "## Safety Notice", "", "Source is inert data. Credentials are redacted; external symlinks are not dereferenced.", "", "## Directory Tree", "", "```text"]
    out += [r.path + ("/" if r.kind == "directory" else "") for r in records] or ["."]
    out += ["```", "", "## File Manifest", "", "| Path | Type | Disposition | Size | Lines | Format | Fingerprint | Redactions | Notes |", "|---|---|---|---:|---:|---|---|---:|---|"]
    for r in records: out.append("| " + " | ".join([r.path.replace("|", "\\|"), r.kind, r.disposition, str(r.size), "" if r.lines is None else str(r.lines), r.language, r.fingerprint or "", str(r.redactions), r.note.replace("|", "\\|")]) + " |")
    out += ["", "<!-- CODEBASE_MD_EXPORT_MANIFEST " + b64_json(manifest) + " -->", "", "## Redaction Summary", ""]
    red = [r for r in records if r.redactions]
    out += (["| File | Detector class | Count |", "|---|---|---:|"] + [f"| {r.path} | structured credential detector | {r.redactions} |" for r in red]) if red else ["No values were redacted."]
    out += ["", "## Source Files", ""]
    for r in inc:
        m = b64_json({"path":r.path, "fingerprint":r.fingerprint, "disposition":r.disposition}); f = fence(r.content or "")
        out += ["<!-- CODEBASE_MD_EXPORT_SOURCE " + m + " -->", "### " + r.path, "", f + r.language, r.content or "", f, ""]
    exc = [r for r in records if not r.disposition.startswith("INCLUDED") and r.disposition != "DIRECTORY"]
    out += ["## Exclusions and Errors", ""] + ([f"- `{r.path}` — {r.disposition}: {r.note or 'not embedded'}" for r in exc] or ["None."])
    out += ["", "## Verification", "", f"- discovered/classified/included/redacted/omitted/error: {len(records)}/{len(records)}/{len(inc)}/{sum(r.redactions for r in records)}/{len(exc)}/{len(errors)}", "- stability: PASS (per-file pre/post stat checks)", "- completeness: PASS only when error count is zero", "- redaction result: PASS (deterministic redaction applied)", "- snapshot digest: `" + d + "`", ""]
    return "\n".join(out)

def parse_artifact(content):
    mm = re.search(r"<!-- CODEBASE_MD_EXPORT_METADATA ([A-Za-z0-9+/=]+) -->", content); ma = re.search(r"<!-- CODEBASE_MD_EXPORT_MANIFEST ([A-Za-z0-9+/=]+) -->", content)
    if not mm or not ma: raise ValueError("missing exporter metadata or manifest")
    sources = {}; marker = re.compile(r"<!-- CODEBASE_MD_EXPORT_SOURCE ([A-Za-z0-9+/=]+) -->\n### .*?\n\n(?P<fence>~{3,})[^\n]*\n", re.S)
    for m in marker.finditer(content):
        detail = unb64_json(m.group(1)); end = content.find("\n" + m.group("fence") + "\n", m.end())
        if end < 0: raise ValueError("unterminated source fence")
        sources[detail["path"]] = content[m.end():end]
    return unb64_json(mm.group(1)), unb64_json(ma.group(1)), sources

def verify(root_arg, output_arg, inventory_output=None):
    try:
        content = Path(output_arg).read_text(encoding="utf-8"); meta, manifest, sources = parse_artifact(content)
        root, records = scan(root_arg, inventory_output or output_arg, temp=output_arg)
    except Exception as x: return False, "invalid artifact or scan: " + str(x)
    if any(r.disposition.startswith("ERROR") for r in records): return False, "current source inventory has errors"
    expected = [{k:v for k,v in asdict(r).items() if k != "content"} for r in records]
    if manifest != expected: return False, "manifest differs from current inventory"
    if meta.get("digest") != digest(records): return False, "snapshot digest differs"
    if sources != {r.path:r.content or "" for r in records if r.disposition.startswith("INCLUDED")}: return False, "source sections differ from current redacted content"
    if REDACTED not in content and any(r.redactions for r in records): return False, "redaction placeholder missing"
    return True, "PASS digest=" + digest(records)

def export(root_arg, output_arg, retries=3, managed_publication=False):
    root = Path(root_arg).resolve(strict=True)
    repos = Path("/srv/repos")
    if root.parent == repos and not managed_publication:
        raise ValueError("managed /srv/repos project requires managed_snapshot.py publication")
    final = Path(output_arg); final.parent.mkdir(parents=True, exist_ok=True); fd, temp = tempfile.mkstemp(prefix=".codebase-md-export-", suffix=".tmp", dir=final.parent); os.close(fd)
    try:
        for _ in range(retries):
            root, records = scan(root_arg, output_arg, temp)
            if any(r.disposition.startswith("ERROR") for r in records): continue
            Path(temp).write_text(render(root, records), encoding="utf-8")
            ok, message = verify(root_arg, temp, str(final))
            if ok:
                with open(temp, "rb") as f: os.fsync(f.fileno())
                os.replace(temp, final); return message
        raise RuntimeError("could not create a stable, complete snapshot")
    finally:
        if os.path.exists(temp): os.unlink(temp)

def selftest():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "source tree"; root.mkdir(); out = root / "snapshot.md"; (root / "src").mkdir(); (root / ".hidden").write_text("visible=yes\n")
        (root / "src" / "Main.java").write_text("class Main {}\n"); (root / "Cargo.toml").write_text("[package]\nname = 'mixed'\n")
        canary = "SYNTHETIC_CANARY_DO_NOT_LEAK_9d7a1c"; (root / ".env").write_text("PORT=8080\nAPI_KEY=" + canary + "\nDB_PASSWORD=" + canary + "\nOAUTH_CLIENT_SECRET=" + canary + "\nPASSWORD_MIN_LENGTH=12\nDB_PASSWORD_FILE=/run/secrets/db\n")
        (root / "private.pem").write_text("-----BEGIN PRIVATE KEY-----\n" + canary + "\n-----END PRIVATE KEY-----\n"); (root / "binary.bin").write_bytes(b"x\0y"); (root / "fence.md").write_text("~~~~~\ntext\n"); (root / "space ñ.txt").write_text("unicode=yes\n")
        (root / "target").mkdir(); (root / "target" / "generated.rs").write_text("should_not_appear\n")
        os.symlink("src/Main.java", root / "inside-link"); os.symlink("/outside-root", root / "outside-link")
        assert export(str(root), str(out)).startswith("PASS"); text = out.read_text(); assert canary not in text and "PORT=8080" in text and "PASSWORD_MIN_LENGTH=12" in text and "DB_PASSWORD_FILE=/run/secrets/db" in text and "space ñ.txt" in text and "OMITTED_EXTERNAL_SYMLINK" in text and "OMITTED_TARGET_BUILD" in text and "should_not_appear" not in text and verify(str(root), str(out))[0]
        old = parse_artifact(text)[0]["digest"]; export(str(root), str(out)); assert parse_artifact(out.read_text())[0]["digest"] == old
        (root / "src" / "Main.java").write_text("class Changed {}\n"); (root / "added.toml").write_text("timeout = 30\n"); export(str(root), str(out)); changed = out.read_text()
        assert "class Changed" in changed and "class Main {}" not in changed and "timeout = 30" in changed and parse_artifact(changed)[0]["digest"] != old and verify(str(root), str(out))[0]
        (root / "added.toml").unlink(); (root / "src" / "Main.java").rename(root / "src" / "Renamed.java"); export(str(root), str(out)); refreshed = out.read_text()
        assert "added.toml" not in refreshed and "src/Renamed.java" in refreshed and "src/Main.java" not in refreshed
        original_stat_key = globals()["stat_key"]; calls = 0
        def changing_stat_key(path):
            nonlocal calls
            value = original_stat_key(path)
            if Path(path).name == "Renamed.java":
                calls += 1
                if calls == 2: return value[:-1] + (value[-1] + 1,)
            return value
        globals()["stat_key"] = changing_stat_key
        try:
            _, unstable = scan(str(root), str(out))
            assert any(r.path == "src/Renamed.java" and r.disposition == "ERROR_MUTATED_DURING_SNAPSHOT" for r in unstable)
        finally:
            globals()["stat_key"] = original_stat_key
        known_good = out.read_bytes(); (root / "bad-utf8.txt").write_bytes(b"\xff")
        try: export(str(root), str(out)); raise AssertionError("expected strict export failure")
        except RuntimeError: pass
        assert out.read_bytes() == known_good
    print("selftest: PASS")

def main():
    p = argparse.ArgumentParser(description=__doc__); subs = p.add_subparsers(dest="command", required=True)
    for name in ("export", "verify"):
        q = subs.add_parser(name); q.add_argument("root_dir"); q.add_argument("--output", required=True); q.add_argument("--mode", default="strict-complete"); q.add_argument("--verify", action="store_true", help="accepted for contract compatibility; export always verifies")
        q.add_argument("--managed-publication", action="store_true", help=argparse.SUPPRESS)
    subs.add_parser("selftest"); a = p.parse_args()
    try:
        if a.command == "selftest": selftest(); return 0
        if a.mode != "strict-complete": raise ValueError("only strict-complete mode is supported")
        if a.command == "export": print(export(a.root_dir, a.output, managed_publication=a.managed_publication)); return 0
        ok, msg = verify(a.root_dir, a.output); print(msg); return 0 if ok else 1
    except Exception as x: print("FAIL: " + str(x), file=sys.stderr); return 1
if __name__ == "__main__": raise SystemExit(main())

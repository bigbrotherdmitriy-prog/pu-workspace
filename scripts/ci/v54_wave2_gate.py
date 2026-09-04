"""Read-only integration gate for the four isolated v5.4 wave-two streams.

The gate reads Git objects and writes one deliberately content-free JSON report.
It does not checkout, merge, execute candidate code, read environment files, or
connect to databases/providers.  A PASS is structural review only; it is not a
substitute for pytest, PostgreSQL, browser E2E, or semantic code review.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


SCHEMA_VERSION = "pu-workspace.v54-wave2-gate.1"
GIT_TIMEOUT_SECONDS = 15
MAX_GIT_OUTPUT_BYTES = 2_000_000
MAX_CHANGED_FILES = 250
MAX_FILE_BYTES = 750_000
SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")

MIGRATION_REVISIONS = (
    ("a54f001c0a02", "a54f001c0a01"),
    ("a54f001c0a03", "a54f001c0a02"),
    ("a54f001c0a04", "a54f001c0a03"),
)

SECRET_BASENAMES = re.compile(
    r"(?:^\.env(?:\..*)?$|^(?:.*[-_.])?(?:secret|secrets|credential|credentials)(?:[-_.].*)?$|"
    r"^id_rsa(?:\..*)?$|^id_ed25519(?:\..*)?$|\.pem$|\.key$|\.p12$|\.pfx$)",
    re.IGNORECASE,
)
REAL_EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})(?![\w.-])")
OAUTH_SECRET_RE = re.compile(r"(?:ya29\.|1//[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._~-]{12,})")
DOCUMENT_MATERIAL_RE = re.compile(r"(?:%PDF-\d|JVBERi0|data:application/(?:pdf|octet-stream);base64)", re.IGNORECASE)
PROVIDER_ID_RE = re.compile(
    r"(?i)\b(?:provider|gmail|drive|external|account|message)[A-Za-z0-9_]*_id\b\s*[:=]\s*['\"]([^'\"]+)['\"]"
)

SENSITIVE_JOB_KEYS = {
    "body", "content", "attachment_bytes", "bytes", "base64", "token",
    "access_token", "refresh_token", "password", "dsn", "database_url",
    "filesystem_path", "absolute_path", "file_path", "path",
}
SENSITIVE_DESCRIPTOR_KEYS = {
    "filename", "file_name", "url", "owner", "owner_id", "project",
    "project_id", "plaintext", "plaintext_metadata", "metadata", "path",
    "filesystem_path", "absolute_path",
}

MAILBOX_ALLOWED_PATHS = frozenset({
    "backend/app/api/ai_secretary.py",
    "backend/app/api/gmail.py",
    "backend/app/api/google_drive.py",
    "backend/app/core/v54_authority.py",
    "backend/app/core/v54_refs.py",
    "backend/app/integrations/google_workspace.py",
    "backend/app/mailbox_identity/__init__.py",
    "backend/app/mailbox_identity/dto.py",
    "backend/app/mailbox_identity/oauth.py",
    "backend/app/mailbox_identity/runtime.py",
    "backend/app/mailbox_identity/service.py",
    "backend/app/models/__init__.py",
    "backend/app/models/ai_secretary.py",
    "backend/app/models/mailbox_identity.py",
    "backend/app/schema.py",
    "backend/migrations/versions/a54f001c0a03_v54_mailbox_identity_expand.py",
    "backend/migrations/versions/a54f001c0a04_v54_mailbox_dedup_cutover.py",
    "backend/tests/test_v54_mailbox_identity.py",
    "backend/tests/test_v54_pilot_foundation.py",
    "docs/architecture/v54/mailbox-cutover/README.md",
    "docs/audits/v54-mailbox-cutover.md",
    "docs/audits/v54-mailbox-identity-implementation.md",
    "scripts/audits/v54_mailbox_inventory.py",
})


class GateFailure(RuntimeError):
    """An expected safe failure whose details must not enter the protocol."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    code: str
    stream: str | None = None


@dataclass(frozen=True)
class Candidate:
    name: str
    sha: str
    files: tuple[str, ...]


def _git(repo: Path, args: Sequence[str], *, limit: int = MAX_GIT_OUTPUT_BYTES) -> bytes:
    command = ["git", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("git_timeout") from exc
    except OSError as exc:
        raise GateFailure("git_unavailable") from exc
    if len(completed.stdout) > limit:
        raise GateFailure("git_output_limit")
    if completed.returncode != 0:
        raise GateFailure("git_command_failed")
    return completed.stdout


def _resolve_commit(repo: Path, value: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise GateFailure("invalid_sha")
    resolved = _git(repo, ["rev-parse", "--verify", f"{value}^{{commit}}"], limit=256).decode("ascii").strip()
    if resolved.lower() != value.lower():
        raise GateFailure("sha_not_exact")
    return resolved.lower()


def _is_ancestor(repo: Path, base: str, candidate: str) -> bool:
    try:
        _git(repo, ["merge-base", "--is-ancestor", base, candidate], limit=64)
        return True
    except GateFailure as exc:
        if exc.code == "git_command_failed":
            return False
        raise


def _changed_files(repo: Path, base: str, candidate: str) -> tuple[str, ...]:
    raw = _git(repo, ["diff", "--name-only", "-z", base, candidate, "--"])
    values = [part.decode("utf-8", "strict") for part in raw.split(b"\0") if part]
    if len(values) > MAX_CHANGED_FILES:
        raise GateFailure("changed_file_limit")
    normalized: list[str] = []
    for value in values:
        path = value.replace("\\", "/")
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise GateFailure("unsafe_git_path")
        normalized.append(path)
    return tuple(normalized)


def _file_bytes(repo: Path, sha: str, path: str) -> bytes | None:
    try:
        return _git(repo, ["cat-file", "-p", f"{sha}:{path}"], limit=MAX_FILE_BYTES)
    except GateFailure as exc:
        if exc.code == "git_command_failed":
            return None
        raise


def _file_text(repo: Path, sha: str, path: str) -> str | None:
    value = _file_bytes(repo, sha, path)
    if value is None:
        return None
    try:
        return value.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise GateFailure("non_utf8_candidate_file") from exc


def _added_text(repo: Path, base: str, candidate: str, path: str) -> str:
    raw = _git(repo, ["diff", "--no-ext-diff", "--no-textconv", "--unified=0", base, candidate, "--", path])
    text = raw.decode("utf-8", "replace")
    return "\n".join(line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))


def _mailbox_allowed(path: str) -> bool:
    return path in MAILBOX_ALLOWED_PATHS


def _stream_allowed(stream: str, path: str) -> bool:
    if stream == "mailbox":
        return _mailbox_allowed(path)
    if stream == "staging":
        return path.startswith("backend/app/staging/") or (
            path.startswith("backend/tests/") and "staging" in PurePosixPath(path).name.lower()
        )
    if stream == "evidence":
        return path in {
            "backend/app/source_evidence/fragment_reader.py",
            "backend/tests/test_v54_fragment_reader.py",
        }
    if stream == "ui":
        return path.startswith("frontend/src/modules/evidence/")
    return False


def _check_paths(candidate: Candidate) -> bool:
    return bool(candidate.files) and all(_stream_allowed(candidate.name, path) for path in candidate.files)


def _secret_path(path: str) -> bool:
    return any(SECRET_BASENAMES.search(part) for part in PurePosixPath(path).parts)


def _parse_python(text: str) -> ast.AST | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _literal_dict_keys(node: ast.AST) -> set[str]:
    keys: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Dict):
            for key in child.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value.lower())
    return keys


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets: Iterable[ast.expr]
    if isinstance(node, ast.Assign):
        targets = node.targets
    else:
        targets = (node.target,)
    result: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            result.add(target.id.lower())
    return result


def _has_sensitive_job_payload(text: str) -> bool:
    tree = _parse_python(text)
    if tree is None:
        return False
    payload_bindings: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is not None:
                for name in _assigned_names(node):
                    if "payload" in name:
                        payload_bindings[name] = value
        if isinstance(node, ast.ClassDef) and "payload" in node.name.lower():
            fields = {child.target.id.lower() for child in node.body if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)}
            if fields & SENSITIVE_JOB_KEYS:
                return True
    for value in payload_bindings.values():
        if _literal_dict_keys(value) & SENSITIVE_JOB_KEYS:
            return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
        if function not in {"enqueue", "enqueue_job", "submit_job"}:
            continue
        candidates = list(node.args[1:]) + [item.value for item in node.keywords if item.arg in {"payload", "job_payload"}]
        for value in candidates:
            if isinstance(value, ast.Name) and value.id.lower() in payload_bindings:
                value = payload_bindings[value.id.lower()]
            if _literal_dict_keys(value) & SENSITIVE_JOB_KEYS:
                return True
    return False


def _has_sensitive_descriptor(text: str) -> bool:
    tree = _parse_python(text)
    if tree is None:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and "descriptor" in node.name.lower():
            fields = {child.target.id.lower() for child in node.body if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)}
            if fields & SENSITIVE_DESCRIPTOR_KEYS:
                return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = _assigned_names(node)
            value = node.value
            if value is not None and any("descriptor" in name for name in names):
                if _literal_dict_keys(value) & SENSITIVE_DESCRIPTOR_KEYS:
                    return True
        if isinstance(node, ast.FunctionDef) and "descriptor" in node.name.lower():
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and child.value is not None:
                    if _literal_dict_keys(child.value) & SENSITIVE_DESCRIPTOR_KEYS:
                        return True
    return False


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return ("/tests/" in lowered or name.startswith("test_") or ".test." in name
            or ".spec." in name or "/__tests__/" in lowered)


def _is_product_source(path: str) -> bool:
    if _is_test_path(path):
        return False
    return ((path.startswith("backend/app/") and path.endswith(".py"))
            or (path.startswith("frontend/src/") and PurePosixPath(path).suffix.lower() in {".ts", ".tsx", ".js", ".jsx"}))


def _activation_name(name: str) -> bool:
    parts = name.lower().strip("_").split("_")
    return ("enabled" in parts or "auto" in parts or "autonomy" in parts
            or ("external" in parts and bool({"action", "actions"} & set(parts))))


def _added_default_activation(text: str) -> bool:
    assignment = re.compile(
        r"(?im)^\s*(?:(?:const|let|var)\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*(?::[^=\n]+)?=\s*(?P<value>[^\n#]+)"
    )
    truthy = re.compile(
        r"^(?:True|true|1|['\"](?:true|on|yes|auto)['\"])(?:\s*[,;)]|\s*$)|"
        r"\b(?:default|server_default)\s*=\s*(?:True|true|1|['\"](?:true|on|yes|auto)['\"])",
        re.IGNORECASE,
    )
    for match in assignment.finditer(text):
        if _activation_name(match.group("name")) and truthy.search(match.group("value").strip()):
            return True
    patterns = (
        r"(?i)os\.getenv\([^,]+,\s*['\"](?:true|on|yes|1|auto)['\"]\)",
        r"(?i)\$\{[A-Za-z0-9_]+:-(?:true|on|yes|1|auto)\}",
        r"(?i)['\"]mode['\"]\s*:\s*['\"]AUTO['\"]",
        r"(?i)['\"]enabled['\"]\s*:\s*(?:True|true|1|['\"](?:true|on|yes)['\"])",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _adds_duplicate_engine(text: str) -> bool:
    patterns = (
        r"(?m)^\s*class\s+BackgroundJob\b",
        r"(?m)^\s*class\s+\w*Queue\b",
        r"(?m)^\s*class\s+\w*Ledger\b",
        r"(?m)^\s*class\s+SourceReference\b",
        r"(?m)^\s*class\s+\w*SourceRegistry\b",
        r"(?i)__tablename__\s*=\s*['\"](?:background_jobs|.*ledger.*|.*source_registry.*|v54_sources|source_references)['\"]",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _reads_forbidden_runtime_state(text: str) -> bool:
    patterns = (
        r"\bload_dotenv\b|\bdotenv_values\b|\bfind_dotenv\b",
        r"(?i)(?:open|Path)\s*\(\s*['\"]\.env",
        r"(?i)os\.(?:getenv|environ(?:\.get)?)\s*\([^\n]*(?:DATABASE_URL|PRODUCTION_DATABASE|CREDENTIAL)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _ui_has_forbidden_behavior(text: str) -> bool:
    patterns = (
        r"\bfetch\s*\(", r"\bXMLHttpRequest\b", r"\blocalStorage\b",
        r"dangerouslySetInnerHTML", r"(?i)https?://", r"['\"]/(?:api|auth)/",
        r"(?i)<\s*(?:button|form|input|select|textarea)\b", r"\bonClick\s*=", r"\bonSubmit\s*=",
        r"(?i)\bmethod\s*:\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _test_has_unsafe_fixture(text: str) -> bool:
    for match in REAL_EMAIL_RE.finditer(text):
        address = match.group(1).lower()
        if not address.endswith("@example.test") and not address.endswith("@example.invalid"):
            return True
    if OAUTH_SECRET_RE.search(text) or DOCUMENT_MATERIAL_RE.search(text):
        return True
    safe_markers = ("synthetic", "fake", "test", "example", "sample")
    return any(
        len(match.group(1)) >= 10 and not any(marker in match.group(1).lower() for marker in safe_markers)
        for match in PROVIDER_ID_RE.finditer(text)
    )


def _call_name(node: ast.Call) -> str:
    values: list[str] = []
    current: ast.AST = node.func
    while isinstance(current, ast.Attribute):
        values.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        values.append(current.id)
    return ".".join(reversed(values))


def _test_skip_usage(text: str) -> tuple[bool, bool, bool]:
    """Return unconditional, unsupported-conditional and allowed-conditional use."""
    tree = _parse_python(text)
    if tree is None:
        hidden = bool(re.search(
            r"\b(?:it|test|describe)\.skip\s*\(|\b(?:xit|xtest|xdescribe)\s*\(", text,
        ))
        return hidden, False, False

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def ancestors(node: ast.AST) -> Iterable[ast.AST]:
        while node in parents:
            node = parents[node]
            yield node

    def source(node: ast.AST) -> str:
        return (ast.get_source_segment(text, node) or ast.dump(node)).lower()

    def explicit_condition(value: str) -> bool:
        markers = (
            "sys.platform", "os.name", "platform.system", "hasattr(os", "symlink", "hardlink",
            "os.link", "oserror", "notimplementederror", "postgres", "psycopg",
            "test_database_url", "database_url",
        )
        return any(marker in value for marker in markers)

    unconditional = unsupported = allowed = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                value = source(decorator)
                if "pytest.mark.xfail" in value or re.search(r"(?:pytest\.mark\.|unittest\.)skip(?:\s*\(|$)", value):
                    unconditional = True
                elif "pytest.mark.skipif" in value:
                    if explicit_condition(value):
                        allowed = True
                    else:
                        unsupported = True
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in {"pytest.xfail", "unittest.skip"}:
            unconditional = True
            continue
        if name != "pytest.skip":
            continue
        chain = tuple(ancestors(node))
        platform_handler = any(
            isinstance(parent, ast.ExceptHandler)
            and explicit_condition(source(parent.type) if parent.type is not None else "")
            for parent in chain
        )
        guarded = next((parent for parent in chain if isinstance(parent, ast.If)), None)
        reason = source(node)
        if platform_handler or (guarded is not None and explicit_condition(source(guarded.test))):
            allowed = True
        elif guarded is not None and explicit_condition(reason + " " + source(guarded)):
            allowed = True
        elif any(isinstance(parent, (ast.If, ast.ExceptHandler)) for parent in chain):
            unsupported = True
        else:
            unconditional = True
    return unconditional, unsupported, allowed


def _extract_revision(text: str, field: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(field)}\s*=\s*['\"]([^'\"]+)['\"]\s*$", text)
    return match.group(1) if match else None


def _migration_chain_ok(repo: Path, mailbox: Candidate) -> bool:
    migration_files = [path for path in mailbox.files if path.startswith("backend/migrations/versions/")]
    if len(migration_files) != 2:
        return False
    tree = _git(repo, ["ls-tree", "-r", "--name-only", mailbox.sha, "--", "backend/migrations/versions"])
    tree_paths = [value for value in tree.decode("utf-8", "strict").splitlines() if value]
    base_revision_files = [path for path in tree_paths if "a54f001c0a02" in PurePosixPath(path).name]
    relevant_files = [*base_revision_files, *migration_files]
    if len(base_revision_files) != 1:
        return False
    found: dict[str, str | None] = {}
    for path in relevant_files:
        text = _file_text(repo, mailbox.sha, path)
        if text is None:
            return False
        revision = _extract_revision(text, "revision")
        down_revision = _extract_revision(text, "down_revision")
        if revision is None or revision in found:
            return False
        found[revision] = down_revision
    if any(found.get(revision) != parent for revision, parent in MIGRATION_REVISIONS):
        return False
    schema = _file_text(repo, mailbox.sha, "backend/app/schema.py")
    return schema is not None and bool(re.search(r"CURRENT_SCHEMA_REVISION\s*=\s*['\"]a54f001c0a04['\"]", schema))


def _content_checks(repo: Path, base: str, candidates: dict[str, Candidate]) -> dict[str, bool]:
    result = {
        "duplicate_engine": False,
        "default_activation": False,
        "sensitive_job_payload": False,
        "runtime_state_read": False,
        "staging_descriptor": False,
        "ui_behavior": False,
        "unsafe_fixture": False,
        "unconditional_test_skip": False,
        "unsupported_conditional_skip": False,
        "conditional_runtime_skip": False,
    }
    for candidate in candidates.values():
        for path in candidate.files:
            text = _file_text(repo, candidate.sha, path)
            if text is None:
                continue
            added = _added_text(repo, base, candidate.sha, path)
            result["duplicate_engine"] |= _adds_duplicate_engine(added)
            if _is_product_source(path):
                result["default_activation"] |= _added_default_activation(added)
            if path.endswith(".py") and "/tests/" not in path and not path.startswith("scripts/"):
                result["sensitive_job_payload"] |= _has_sensitive_job_payload(text)
                result["runtime_state_read"] |= _reads_forbidden_runtime_state(added)
            if candidate.name == "staging" and path.startswith("backend/app/staging/"):
                result["staging_descriptor"] |= _has_sensitive_descriptor(text)
            if (candidate.name == "ui" and path.startswith("frontend/src/modules/evidence/")
                    and not _is_test_path(path)):
                result["ui_behavior"] |= _ui_has_forbidden_behavior(text)
            if "/test" in path.lower() or PurePosixPath(path).name.lower().startswith("test_") or ".test." in path.lower():
                result["unsafe_fixture"] |= _test_has_unsafe_fixture(text)
                unconditional, unsupported, conditional = _test_skip_usage(added)
                result["unconditional_test_skip"] |= unconditional
                result["unsupported_conditional_skip"] |= unsupported
                result["conditional_runtime_skip"] |= conditional
    return result


def _check(ok: bool, identifier: str, fail_code: str, *, stream: str | None = None) -> Check:
    return Check(identifier, "pass" if ok else "fail", "ok" if ok else fail_code, stream)


def evaluate(repo: Path, *, base_sha: str, mailbox_sha: str, staging_sha: str,
             evidence_sha: str, ui_sha: str) -> dict[str, object]:
    repo = Path(_git(repo, ["rev-parse", "--show-toplevel"], limit=4096).decode("utf-8").strip())
    base = _resolve_commit(repo, base_sha)
    supplied = {"mailbox": mailbox_sha, "staging": staging_sha, "evidence": evidence_sha, "ui": ui_sha}
    candidates: dict[str, Candidate] = {}
    checks: list[Check] = []
    for name, value in supplied.items():
        try:
            sha = _resolve_commit(repo, value)
            descendant = _is_ancestor(repo, base, sha) and sha != base
            files = _changed_files(repo, base, sha) if descendant else ()
            candidates[name] = Candidate(name, sha, files)
            checks.append(_check(descendant, "sha_descends_from_base", "not_descendant", stream=name))
        except GateFailure as exc:
            checks.append(Check("sha_descends_from_base", "fail", exc.code, name))

    if len(candidates) != 4 or any(not item.files for item in candidates.values()):
        return _protocol(base, checks)

    for name, candidate in candidates.items():
        checks.append(_check(_check_paths(candidate), "stream_path_scope", "path_scope_violation", stream=name))

    mailbox = candidates["mailbox"]
    non_mailbox = [candidates[name] for name in ("staging", "evidence", "ui")]
    mailbox_migrations = [path for path in mailbox.files if path.startswith("backend/migrations/versions/")]
    others_have_migrations = any(path.startswith("backend/migrations/versions/") for item in non_mailbox for path in item.files)
    checks.append(_check(bool(mailbox_migrations) and not others_have_migrations, "migration_owner", "migration_owner_violation"))
    checks.append(_check(_migration_chain_ok(repo, mailbox), "mailbox_migration_chain", "migration_chain_invalid", stream="mailbox"))
    checks.append(_check(all("backend/app/schema.py" not in item.files for item in non_mailbox), "schema_owner", "schema_owner_violation"))

    all_files = [path for candidate in candidates.values() for path in candidate.files]
    forbidden_path = any(
        path == "frontend/src/App.tsx"
        or path.startswith("backend/app/jobs/")
        or PurePosixPath(path).name.lower().startswith("docker-compose")
        or _secret_path(path)
        for path in all_files
    )
    checks.append(_check(not forbidden_path, "forbidden_paths", "forbidden_path"))

    content = _content_checks(repo, base, candidates)
    checks.extend((
        _check(not content["duplicate_engine"], "single_engines", "duplicate_engine_detected"),
        _check(not content["default_activation"], "no_default_activation", "default_activation_detected"),
        _check(not content["sensitive_job_payload"], "opaque_job_payload", "sensitive_job_payload"),
        _check(not content["runtime_state_read"], "no_runtime_secret_reads", "runtime_state_read_detected"),
        _check(not content["staging_descriptor"], "opaque_staging_descriptor", "sensitive_staging_descriptor", stream="staging"),
        _check("backend/app/source_evidence/facade.py" not in candidates["evidence"].files,
               "evidence_facade_unchanged", "source_facade_modified", stream="evidence"),
        _check(not content["ui_behavior"], "ui_read_only", "ui_forbidden_behavior", stream="ui"),
        _check(not content["unsafe_fixture"], "synthetic_test_data", "unsafe_test_fixture"),
        _check(not content["unconditional_test_skip"], "no_unconditional_test_skips", "unconditional_skip_or_xfail_added"),
        _check(not content["unsupported_conditional_skip"], "conditional_test_skip_scope", "unsupported_conditional_skip"),
    ))

    for name, candidate in candidates.items():
        try:
            _git(repo, ["diff", "--check", base, candidate.sha, "--"], limit=200_000)
            ok = True
        except GateFailure:
            ok = False
        checks.append(_check(ok, "git_diff_check", "git_diff_check_failed", stream=name))
    protocol = _protocol(base, checks)
    if content["conditional_runtime_skip"]:
        protocol["limitations"].append("conditional_platform_or_postgres_tests_may_skip")
    return protocol


def _protocol(base: str | None, checks: Sequence[Check], *, fatal_code: str | None = None) -> dict[str, object]:
    passed = sum(item.status == "pass" for item in checks)
    failed = sum(item.status == "fail" for item in checks)
    protocol: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if failed == 0 and fatal_code is None else "fail",
        "base_sha": base,
        "summary": {"passed": passed, "failed": failed, "total": len(checks)},
        "checks": [asdict(item) for item in checks],
        "limitations": ["structural_only", "candidate_code_not_executed", "postgres_not_tested", "browser_e2e_not_tested"],
    }
    if fatal_code is not None:
        protocol["fatal_code"] = fatal_code
    return protocol


def _safe_failure(code: str) -> dict[str, object]:
    return _protocol(None, (), fatal_code=code)


def write_protocol(path: Path, protocol: dict[str, object]) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(protocol, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Read-only structural gate for PU Workspace v5.4 wave two")
    result.add_argument("--base-sha", required=True)
    result.add_argument("--mailbox-sha", required=True)
    result.add_argument("--staging-sha", required=True)
    result.add_argument("--evidence-sha", required=True)
    result.add_argument("--ui-sha", required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        protocol = evaluate(
            Path.cwd(), base_sha=arguments.base_sha, mailbox_sha=arguments.mailbox_sha,
            staging_sha=arguments.staging_sha, evidence_sha=arguments.evidence_sha, ui_sha=arguments.ui_sha,
        )
    except GateFailure as exc:
        protocol = _safe_failure(exc.code)
    except Exception:
        protocol = _safe_failure("internal_error")
    try:
        write_protocol(arguments.output, protocol)
    except OSError:
        return 2
    return 0 if protocol["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

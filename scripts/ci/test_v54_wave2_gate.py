from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.ci import v54_wave2_gate as gate


def _run(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        shell=False,
    )
    return completed.stdout.strip()


@dataclass(frozen=True)
class Wave:
    repo: Path
    root: str
    base: str
    mailbox: str
    staging: str
    evidence: str
    ui: str


def _write(repo: Path, path: str, content: str) -> None:
    destination = repo / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8", newline="\n")


def _commit(repo: Path, message: str) -> str:
    _run(repo, "add", "--all")
    _run(repo, "commit", "-m", message)
    return _run(repo, "rev-parse", "HEAD")


def _migration(revision: str, down_revision: str) -> str:
    return f'revision = "{revision}"\ndown_revision = "{down_revision}"\n'


def _make_wave(
    tmp_path: Path,
    *,
    mailbox_extra: dict[str, str] | None = None,
    staging_extra: dict[str, str] | None = None,
    evidence_extra: dict[str, str] | None = None,
    ui_extra: dict[str, str] | None = None,
) -> Wave:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "--initial-branch=main")
    _run(repo, "config", "user.name", "Gate Test")
    _run(repo, "config", "user.email", "gate@example.test")
    _write(repo, "README.md", "synthetic repository\n")
    root = _commit(repo, "root")
    _write(repo, "backend/app/schema.py", 'CURRENT_SCHEMA_REVISION = "a54f001c0a02"\n')
    _write(
        repo,
        "backend/migrations/versions/a54f001c0a02_authority.py",
        _migration("a54f001c0a02", "a54f001c0a01"),
    )
    _write(repo, "frontend/src/App.tsx", "export function App() { return null; }\n")
    _write(repo, "backend/app/source_evidence/facade.py", "FRAGMENT_DENY = True\n")
    base = _commit(repo, "base")

    streams: list[tuple[str, dict[str, str]]] = [
        (
            "mailbox",
            {
                "backend/app/schema.py": 'CURRENT_SCHEMA_REVISION = "a54f001c0a04"\n',
                "backend/app/mailbox_identity/runtime.py": "MAILBOX_IDENTITY_CUTOVER = False\n",
                "backend/migrations/versions/a54f001c0a03_v54_mailbox_identity_expand.py": _migration("a54f001c0a03", "a54f001c0a02"),
                "backend/migrations/versions/a54f001c0a04_v54_mailbox_dedup_cutover.py": _migration("a54f001c0a04", "a54f001c0a03"),
                **(mailbox_extra or {}),
            },
        ),
        (
            "staging",
            {
                "backend/app/staging/descriptor.py": 'STAGING_DESCRIPTOR = {"opaque_ref": "stg_01"}\n',
                "backend/tests/test_v54_staging_core.py": "def test_staging_contract():\n    assert True\n",
                **(staging_extra or {}),
            },
        ),
        (
            "evidence",
            {
                "backend/app/source_evidence/fragment_reader.py": "def read_fragment():\n    return None\n",
                "backend/tests/test_v54_fragment_reader.py": "def test_fragment_reader():\n    assert True\n",
                **(evidence_extra or {}),
            },
        ),
        (
            "ui",
            {
                "frontend/src/modules/evidence/EvidenceFragmentCard.tsx": (
                    "export function EvidenceFragmentCard() { return <article>Evidence</article>; }\n"
                ),
                "frontend/src/modules/evidence/EvidenceFragmentCard.test.tsx": (
                    "test('renders safe evidence', () => { expect(true).toBe(true); });\n"
                ),
                **(ui_extra or {}),
            },
        ),
    ]
    shas: dict[str, str] = {}
    for name, files in streams:
        _run(repo, "checkout", "--detach", base)
        for path, content in files.items():
            _write(repo, path, content)
        shas[name] = _commit(repo, name)
    return Wave(repo, root, base, shas["mailbox"], shas["staging"], shas["evidence"], shas["ui"])


def _evaluate(wave: Wave, **replacements: str) -> dict[str, object]:
    values = {
        "mailbox_sha": wave.mailbox,
        "staging_sha": wave.staging,
        "evidence_sha": wave.evidence,
        "ui_sha": wave.ui,
        **replacements,
    }
    return gate.evaluate(wave.repo, base_sha=wave.base, **values)


def _failure_codes(result: dict[str, object]) -> set[str]:
    return {
        item["code"]
        for item in result["checks"]
        if isinstance(item, dict) and item.get("status") == "fail"
    }


def test_accepts_a_correct_synthetic_wave(tmp_path: Path) -> None:
    result = _evaluate(_make_wave(tmp_path))

    assert result["status"] == "pass", _failure_codes(result)
    assert result["summary"]["failed"] == 0


def test_rejects_file_owned_by_another_stream(tmp_path: Path) -> None:
    wave = _make_wave(tmp_path, mailbox_extra={"frontend/src/foreign.ts": "export {};\n"})

    assert "path_scope_violation" in _failure_codes(_evaluate(wave))


def test_rejects_branched_mailbox_migrations(tmp_path: Path) -> None:
    wave = _make_wave(
        tmp_path,
        mailbox_extra={
            "backend/migrations/versions/a54f001c0a04_v54_mailbox_dedup_cutover.py": _migration("a54f001c0a04", "a54f001c0a02")
        },
    )

    assert "migration_chain_invalid" in _failure_codes(_evaluate(wave))


def test_rejects_auto_enabled_by_default(tmp_path: Path) -> None:
    wave = _make_wave(
        tmp_path,
        mailbox_extra={"backend/app/mailbox_identity/runtime.py": "AUTO_ACTIONS_ENABLED = True\n"},
    )

    assert "default_activation_detected" in _failure_codes(_evaluate(wave))


def test_rejects_secret_filename(tmp_path: Path) -> None:
    wave = _make_wave(
        tmp_path,
        mailbox_extra={"docs/architecture/v54/mailbox/client-secret.pem": "synthetic marker\n"},
    )

    assert "forbidden_path" in _failure_codes(_evaluate(wave))


def test_rejects_body_and_base64_in_job_payload(tmp_path: Path) -> None:
    source = "def submit(enqueue):\n    payload = {\"body\": \"x\", \"base64\": \"eA==\"}\n    return enqueue(\"mail\", payload)\n"
    wave = _make_wave(tmp_path, mailbox_extra={"backend/app/mailbox_identity/runtime.py": source})

    assert "sensitive_job_payload" in _failure_codes(_evaluate(wave))


def test_rejects_app_tsx_modification(tmp_path: Path) -> None:
    wave = _make_wave(tmp_path, ui_extra={"frontend/src/App.tsx": "export const App = () => 'changed';\n"})

    assert "forbidden_path" in _failure_codes(_evaluate(wave))


def test_rejects_staging_url_or_path_handle(tmp_path: Path) -> None:
    source = 'STAGING_DESCRIPTOR = {"url": "synthetic", "path": "opaque-looking-but-forbidden"}\n'
    wave = _make_wave(tmp_path, staging_extra={"backend/app/staging/descriptor.py": source})

    assert "sensitive_staging_descriptor" in _failure_codes(_evaluate(wave))


def test_rejects_ui_fetch_and_dangerous_html(tmp_path: Path) -> None:
    source = "export function Card() { fetch('/api/x'); return <div dangerouslySetInnerHTML={{__html: 'x'}} />; }\n"
    wave = _make_wave(
        tmp_path,
        ui_extra={"frontend/src/modules/evidence/EvidenceFragmentCard.tsx": source},
    )

    assert "ui_forbidden_behavior" in _failure_codes(_evaluate(wave))


def test_rejects_source_facade_modification(tmp_path: Path) -> None:
    wave = _make_wave(
        tmp_path,
        evidence_extra={"backend/app/source_evidence/facade.py": "FRAGMENT_DENY = False\n"},
    )

    result = _evaluate(wave)
    assert result["status"] == "fail"
    assert {"path_scope_violation", "source_facade_modified"} & _failure_codes(result)


def test_rejects_non_descendant_commit(tmp_path: Path) -> None:
    wave = _make_wave(tmp_path)

    assert "not_descendant" in _failure_codes(_evaluate(wave, ui_sha=wave.root))


def test_rejects_provider_identifiers_and_embedded_documents_in_tests(tmp_path: Path) -> None:
    source = 'PROVIDER_MESSAGE_ID = "17fa40b9ac21d030"\nPDF_BYTES = "JVBERi0xLjQ="\n'
    wave = _make_wave(
        tmp_path,
        mailbox_extra={"backend/tests/test_v54_mailbox_identity.py": source},
    )

    assert "unsafe_test_fixture" in _failure_codes(_evaluate(wave))


def test_failure_protocol_is_safe_and_omits_secret_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "SYNTHETIC_SECRET_DO_NOT_LEAK_8675309"
    source = f"export const value = '{marker}'; fetch('/api/private');\n"
    wave = _make_wave(
        tmp_path,
        ui_extra={"frontend/src/modules/evidence/EvidenceFragmentCard.tsx": source},
    )
    output = tmp_path / "result.json"
    monkeypatch.chdir(wave.repo)

    exit_code = gate.main([
        "--base-sha", wave.base,
        "--mailbox-sha", wave.mailbox,
        "--staging-sha", wave.staging,
        "--evidence-sha", wave.evidence,
        "--ui-sha", wave.ui,
        "--output", str(output),
    ])

    raw = output.read_text(encoding="utf-8")
    protocol = json.loads(raw)
    assert exit_code == 1
    assert protocol["status"] == "fail"
    assert marker not in raw
    assert "EvidenceFragmentCard" not in raw
    assert "raw_stderr" not in raw.lower()
    assert "diff_body" not in raw.lower()
    assert "files" not in protocol


def test_mailbox_scope_is_the_exact_reviewed_write_set() -> None:
    assert gate.MAILBOX_ALLOWED_PATHS == {
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
    }
    assert not gate._mailbox_allowed("backend/app/models/mailbox_surprise.py")
    assert not gate._mailbox_allowed("backend/tests/test_gmail_unreviewed.py")


def test_ai_secretary_is_not_a_secret_basename() -> None:
    assert not gate._secret_path("backend/app/api/ai_secretary.py")
    assert gate._secret_path("docs/architecture/v54/mailbox/client-secret.pem")
    assert gate._secret_path("backend/app/credentials.json")


def test_activation_detector_targets_product_defaults_not_incidental_text(tmp_path: Path) -> None:
    first = tmp_path / "model"
    first.mkdir()
    wave = _make_wave(
        first,
        mailbox_extra={"backend/app/models/mailbox_identity.py": "id = mapped_column(autoincrement=True)\n"},
    )
    assert "default_activation_detected" not in _failure_codes(_evaluate(wave))

    second = tmp_path / "docs"
    second.mkdir()
    wave = _make_wave(
        second,
        mailbox_extra={"docs/audits/v54-mailbox-cutover.md": "AUTO_ACTIONS_ENABLED = True is forbidden\n"},
    )
    assert "default_activation_detected" not in _failure_codes(_evaluate(wave))


def test_ui_negative_test_strings_are_not_product_behavior(tmp_path: Path) -> None:
    wave = _make_wave(
        tmp_path,
        ui_extra={
            "frontend/src/modules/evidence/EvidenceFragmentCard.test.tsx": (
                "test('negative strings', () => { expect('fetch localStorage dangerouslySetInnerHTML').toBeTruthy(); });\n"
            )
        },
    )
    assert "ui_forbidden_behavior" not in _failure_codes(_evaluate(wave))


def test_allows_explicit_platform_skip_but_marks_runtime_limitation(tmp_path: Path) -> None:
    source = (
        "import os\nimport pytest\n\n"
        "def test_platform_link():\n"
        "    try:\n        os.link('synthetic-a', 'synthetic-b')\n"
        "    except OSError:\n        pytest.skip('hardlinks are unavailable')\n"
    )
    wave = _make_wave(tmp_path, staging_extra={"backend/tests/test_v54_staging_filesystem.py": source})
    result = _evaluate(wave)
    assert "unconditional_skip_or_xfail_added" not in _failure_codes(result)
    assert "unsupported_conditional_skip" not in _failure_codes(result)
    assert "conditional_platform_or_postgres_tests_may_skip" in result["limitations"]


def test_rejects_unconditional_and_unrelated_conditional_skips(tmp_path: Path) -> None:
    unconditional = "import pytest\ndef test_hidden():\n    pytest.skip('not today')\n"
    first = tmp_path / "unconditional"
    first.mkdir()
    wave = _make_wave(first, staging_extra={"backend/tests/test_v54_staging_filesystem.py": unconditional})
    assert "unconditional_skip_or_xfail_added" in _failure_codes(_evaluate(wave))

    unsupported = "import pytest\ndef test_hidden():\n    if feature_ready:\n        pytest.skip('not today')\n"
    second = tmp_path / "unsupported"
    second.mkdir()
    wave = _make_wave(second, staging_extra={"backend/tests/test_v54_staging_filesystem.py": unsupported})
    assert "unsupported_conditional_skip" in _failure_codes(_evaluate(wave))

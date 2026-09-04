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
                "backend/app/api/mailbox_identity.py": "MAILBOX_IDENTITY_CUTOVER = False\n",
                "backend/migrations/versions/a54f001c0a03_mailbox.py": _migration("a54f001c0a03", "a54f001c0a02"),
                "backend/migrations/versions/a54f001c0a04_mailbox.py": _migration("a54f001c0a04", "a54f001c0a03"),
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
                "backend/tests/test_v54_evidence_fragment_reader.py": "def test_fragment_reader():\n    assert True\n",
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

    assert result["status"] == "pass"
    assert result["summary"]["failed"] == 0


def test_rejects_file_owned_by_another_stream(tmp_path: Path) -> None:
    wave = _make_wave(tmp_path, mailbox_extra={"frontend/src/foreign.ts": "export {};\n"})

    assert "path_scope_violation" in _failure_codes(_evaluate(wave))


def test_rejects_branched_mailbox_migrations(tmp_path: Path) -> None:
    wave = _make_wave(
        tmp_path,
        mailbox_extra={
            "backend/migrations/versions/a54f001c0a04_mailbox.py": _migration("a54f001c0a04", "a54f001c0a02")
        },
    )

    assert "migration_chain_invalid" in _failure_codes(_evaluate(wave))


def test_rejects_auto_enabled_by_default(tmp_path: Path) -> None:
    wave = _make_wave(
        tmp_path,
        mailbox_extra={"backend/app/api/mailbox_identity.py": "AUTO_ACTIONS_ENABLED = True\n"},
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
    wave = _make_wave(tmp_path, mailbox_extra={"backend/app/api/mailbox_identity.py": source})

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
        mailbox_extra={"backend/tests/test_v54_mailbox_live_material.py": source},
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

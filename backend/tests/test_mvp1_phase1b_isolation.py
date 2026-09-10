from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parents[1]


def _probe(code: str) -> dict:
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    for name in (
        "APP_SECRET_KEY",
        "BOOTSTRAP_TOKEN",
        "TOKEN_ENCRYPTION_KEY",
        "PU_MODEL_SCOPE",
        "PU_MVP1_OPTIONAL_EXTENSIONS",
    ):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, env=env,
        check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_mvp1_composition_does_not_import_later_mvp_modules():
    result = _probe("""
import json, sys
from app.app_mvp1 import app
banned = (
    'app.task_engine', 'app.response_engine', 'app.governance_engine',
    'app.models.task', 'app.models.response_draft', 'app.models.governance',
    'app.models.execution_finance', 'app.models.project_contact',
    'app.models.ai_secretary',
)
print(json.dumps({'loaded': [name for name in sys.modules if name.startswith(banned)]}))
""")
    assert result == {"loaded": []}


def test_mvp1_composition_exposes_canonical_phase1_resources_only():
    result = _probe("""
import json
from app.app_mvp1 import app
paths = sorted({route.path for route in app.routes})
print(json.dumps({'paths': paths}))
""")
    paths = set(result["paths"])
    assert {"/proposals", "/rules", "/rollbacks/{proposal_id}", "/change-batches"} <= paths
    assert "/change-batches/{proposal_id}/apply" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/analyze" not in paths
    assert "/projects/{project_id}/contracts/{contract_id}/initialize-control" not in paths
    assert not any(path.startswith(("/tasks", "/responses", "/governance", "/gmail")) for path in paths)


def test_mvp1_post_analysis_bridge_is_an_explicit_noop():
    result = _probe("""
import json
from app.app_mvp1 import app
from app.mvp1_extensions import enabled, run_post_analysis
value = run_post_analysis(object(), 1, 2, [object()])
print(json.dumps({
    'enabled': enabled(), 'tasks': len(value.tasks), 'drafts': len(value.drafts),
    'risks': len(value.risks), 'decisions': len(value.decisions),
}))
""")
    assert result == {
        "enabled": False, "tasks": 0, "drafts": 0, "risks": 0, "decisions": 0,
    }


def test_mvp1_readiness_remains_fail_closed_without_configuration():
    result = _probe("""
import json
from app.core.mvp1_readiness import readiness_report
print(json.dumps(readiness_report()))
""")
    assert result["ready"] is False
    assert result["checks"]["app_secret"]["ok"] is False
    assert result["checks"]["database"]["ok"] is False


def test_named_mvp1_core_files_have_no_eager_later_mvp_imports():
    forbidden = (
        "from app.task_engine", "from app.response_engine", "from app.governance_engine",
        "from app.integrations.telegram", "from app.models.task",
        "from app.models.response_draft", "from app.models.governance",
        "from app.models.execution_finance", "from app.models.project_contact",
        "from app.models.ai_secretary",
    )
    files = (
        "app/api/projects.py", "app/api/documents.py", "app/api/workspace.py", "app/organizer.py",
    )
    for relative in files:
        source = (BACKEND / relative).read_text(encoding="utf-8")
        assert not any(item in source for item in forbidden), relative

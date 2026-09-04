"""The approved candidate push must start both independent validation gates."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("filename", ["durable-queue.yml", "storage-picker-e2e.yml"])
def test_final_candidate_branch_starts_validation(filename):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert "codex/parallel-validation-final" in triggers["push"]["branches"]
    assert "codex/v54-wave4-integration" in triggers["push"]["branches"]
    assert workflow["permissions"] == {"contents": "read"}

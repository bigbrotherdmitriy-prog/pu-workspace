from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_common_ci_uses_read_only_repository_permissions():
    workflow = _workflow(WORKFLOWS / "ci.yml")

    assert workflow["permissions"] == {"contents": "read"}


def test_every_checkout_discards_the_repository_credential():
    checkout_steps: list[tuple[str, dict]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = _workflow(path)
        for job in workflow.get("jobs", {}).values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    checkout_steps.append((path.name, step))

    assert checkout_steps
    for filename, step in checkout_steps:
        assert step.get("with", {}).get("persist-credentials") is False, filename

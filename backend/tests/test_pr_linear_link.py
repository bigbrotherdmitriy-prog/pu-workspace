import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pr_linear_link.py"
SPEC = importlib.util.spec_from_file_location("check_pr_linear_link", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def payload(*, title="[PU-123] Add audit trail", branch="codex/pu-123-audit-trail", body=None):
    return {
        "pull_request": {
            "title": title,
            "body": body
            or "Linear: https://linear.app/pu-workspace-ai/issue/PU-123/add-audit-trail",
            "head": {"ref": branch},
        }
    }


def test_accepts_matching_issue_in_title_branch_and_canonical_url():
    assert MODULE.validate_pull_request(payload()) == []


def test_rejects_missing_linear_url():
    errors = MODULE.validate_pull_request(payload(body="No external issue"))
    assert errors == [
        "PR body must contain a canonical Linear URL under linear.app/pu-workspace-ai/issue/PU-N."
    ]


def test_rejects_wrong_linear_workspace_url():
    errors = MODULE.validate_pull_request(
        payload(body="Linear: https://linear.app/another-team/issue/PU-123/example")
    )
    assert "canonical Linear URL" in errors[0]


def test_rejects_issue_mismatch_between_pr_fields():
    errors = MODULE.validate_pull_request(
        payload(body="Linear: https://linear.app/pu-workspace-ai/issue/PU-124/other")
    )
    assert errors == [
        "Linear issue key must be identical in PR title, PR branch and the canonical Linear URL."
    ]


def test_rejects_multiple_linear_issue_keys():
    errors = MODULE.validate_pull_request(
        payload(
            body=(
                "Linear: https://linear.app/pu-workspace-ai/issue/PU-123/one\n"
                "Related: https://linear.app/pu-workspace-ai/issue/PU-124/two"
            )
        )
    )
    assert errors == ["PR body must reference exactly one Linear issue key."]


def test_rejects_non_codex_branch_and_unbracketed_title():
    errors = MODULE.validate_pull_request(
        payload(title="PU-123 Add audit trail", branch="feature/pu-123-audit-trail")
    )
    assert len(errors) == 2
    assert errors[0].startswith("PR title must start")
    assert errors[1].startswith("PR branch must use")


def test_rejects_codex_branch_without_description():
    errors = MODULE.validate_pull_request(payload(branch="codex/pu-123"))
    assert errors == ["PR branch must use codex/pu-N-description and match the Linear issue key."]


def test_does_not_accept_key_as_part_of_larger_number():
    errors = MODULE.validate_pull_request(
        payload(
            title="[PU-1234] Add audit trail",
            branch="codex/pu-1234-audit-trail",
            body="Linear: https://linear.app/pu-workspace-ai/issue/PU-123/example",
        )
    )
    assert errors == [
        "Linear issue key must be identical in PR title, PR branch and the canonical Linear URL."
    ]


def test_required_security_job_invokes_validator():
    workflow = (SCRIPT.parents[1] / ".github" / "workflows" / "security.yml").read_text(
        encoding="utf-8"
    )
    assert "python scripts/check_pr_linear_link.py" in workflow

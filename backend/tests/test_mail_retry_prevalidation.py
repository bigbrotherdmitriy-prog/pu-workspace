"""Codex fallback after DS37 rejected. Pure guard/static API binding, not HTTP."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.mail_retry import require_mail_retry


@pytest.mark.parametrize("status,current,approved,requested,detail", [
    ("unknown", 2, 2, 2, "mail_send_outcome_unknown_reconciliation_required"),
    ("sent", 2, 2, 2, "mail_retry_not_available"),
    ("approved", 2, 2, 2, "mail_retry_not_available"),
    ("failed", 2, 2, 1, "mail_current_revision_not_approved"),
    ("failed", 2, 1, 2, "mail_current_revision_not_approved"),
    ("failed", 2, None, 2, "mail_current_revision_not_approved"),
])
def test_failed_retry_precondition_preserves_original_draft(status, current, approved, requested, detail):
    draft = SimpleNamespace(status=status, revision=current, approved_revision=approved, send_idempotency_key="synthetic-original-key")
    before = vars(draft).copy()
    with pytest.raises(HTTPException) as exc:
        require_mail_retry(draft, requested)
    assert (exc.value.status_code, exc.value.detail) == (409, detail)
    assert vars(draft) == before


def test_current_failed_approved_revision_is_retryable_without_mutation():
    draft = SimpleNamespace(status="failed", revision=2, approved_revision=2, send_idempotency_key="synthetic-key")
    before = vars(draft).copy()
    require_mail_retry(draft, 2)
    assert vars(draft) == before


def test_retry_route_checks_preconditions_before_any_assignment_or_commit():
    tree = ast.parse((Path(__file__).parents[1] / 'app/api/mail.py').read_text())
    route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'retry_mail_draft')
    guard = next(node for node in ast.walk(route) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'require_mail_retry')
    assert [ast.unparse(arg) for arg in guard.args] == ['draft', 'payload.revision']
    mutations = [node.lineno for node in ast.walk(route) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'draft' for target in node.targets)]
    commits = [node.lineno for node in ast.walk(route) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'commit']
    assert mutations and guard.lineno < min(mutations)
    # The delegated send path owns the transaction commit after all admission checks.
    assert not commits

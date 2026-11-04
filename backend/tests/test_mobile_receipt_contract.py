"""Pure response projection only: does not call an API route or authenticate."""
from types import SimpleNamespace
from app.api.mobile_sync import _command_payload


def test_existing_command_receipt_binds_scope_from_stored_row():
    row = SimpleNamespace(id=9, client_mutation_id='synthetic-command', user_id=1, project_id=17,
                          operation='notification.mark_read', entity_type='notification', entity_id=44,
                          status='applied', result={}, error_code=None, error_message=None,
                          created_at=None, updated_at=None)
    result = _command_payload(row, SimpleNamespace(scalar=lambda statement: None))
    assert result['user_id'] == row.user_id
    assert result['project_id'] == row.project_id
    assert result['client_mutation_id'] == row.client_mutation_id
    assert result['entity_id'] == row.entity_id

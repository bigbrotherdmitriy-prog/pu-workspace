from app.api.ai_secretary import BulkContextConfirmation, IncomingMessage, router as secretary_router
from app.api.tasks import router as task_router


def test_mvp2_inbox_routes_are_registered():
    paths = {route.path for route in secretary_router.routes}
    assert "/ai-secretary/inbox" in paths
    assert "/ai-secretary/inbox/{message_id}/confirm-context" in paths
    assert "/ai-secretary/inbox/confirm-context-bulk" in paths
    assert "/ai-secretary/inbox/{message_id}/status" in paths


def test_external_action_requires_explicit_route():
    paths = {route.path for route in task_router.routes}
    assert "/tasks/{task_id}/approve-external" in paths


def test_incoming_message_defaults_to_manual_source():
    payload = IncomingMessage(project_id=1, source_name="Письмо", content="Просим подготовить ответ до 30.08.2026.")
    assert payload.source_type == "manual"
    assert payload.source_external_id is None


def test_bulk_context_confirmation_dedicated_payload():
    payload = BulkContextConfirmation(message_ids=[3, 4], project_id=2, contract_id=7)
    assert payload.message_ids == [3, 4]
    assert payload.project_id == 2
    assert payload.contract_id == 7

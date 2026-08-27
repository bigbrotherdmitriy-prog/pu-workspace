import base64

from app.api.gmail import GmailSyncRequest, _message_text, router
from app.api.google_drive import SCOPES


def test_gmail_routes_and_scopes_are_explicit():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/gmail/sync" in paths
    assert "/response-drafts/{draft_id}/send-gmail" in paths
    assert "https://www.googleapis.com/auth/gmail.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/gmail.send" in SCOPES


def test_gmail_payload_prefers_plain_text():
    plain = base64.urlsafe_b64encode("Просим направить акт до 30.08.2026".encode()).decode()
    markup = base64.urlsafe_b64encode("<b>Другой текст</b>".encode()).decode()
    payload = {"parts": [
        {"mimeType": "text/html", "body": {"data": markup}},
        {"mimeType": "text/plain", "body": {"data": plain}},
    ]}
    assert _message_text(payload) == "Просим направить акт до 30.08.2026"


def test_gmail_sync_is_bounded():
    request = GmailSyncRequest()
    assert request.query == "newer_than:7d"
    assert request.max_results == 25


def test_oauth_callback_returns_to_new_interface():
    source = __import__("inspect").getsource(__import__("app.api.google_drive", fromlist=["google_callback"]).google_callback)
    assert 'url=f"/new/?oauth=connected&project_id={project_id}"' in source

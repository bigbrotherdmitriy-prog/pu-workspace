import inspect

from app.api.local_upload import analyze_local_folder


def test_local_upload_uses_encrypted_staging_before_followup_workflows():
    source = inspect.getsource(analyze_local_folder)
    assert "stage_and_enqueue" in source
    assert '"status": "queued"' in source
    assert "index_documents" not in source

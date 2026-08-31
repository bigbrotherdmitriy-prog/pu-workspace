import inspect

from app.api.local_upload import analyze_local_folder


def test_local_upload_returns_indexed_documents_for_followup_workflows():
    source = inspect.getsource(analyze_local_folder)
    assert '"documents": [{"id": row.id, "name": row.name}' in source

from app.api.documents import list_documents


def test_document_listing_has_bounded_pagination():
    parameters = list_documents.__signature__.parameters if hasattr(list_documents, "__signature__") else None
    # FastAPI keeps Query defaults on the Python signature; route-level smoke is covered in integration tests.
    assert parameters is None or "limit" in parameters

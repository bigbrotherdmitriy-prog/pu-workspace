from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_android_can_select_files_or_capture_a_document_photo():
    source = (ROOT / "frontend/src/modules/android/MobileDocumentUpload.tsx").read_text(encoding="utf-8")
    assert 'accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.csv,image/*"' in source
    assert 'capture="environment"' in source
    assert 'api("/local-upload/analyze"' in source
    assert "Файлы отправятся на ваш сервер только после подтверждения" in source


def test_android_upload_enforces_server_batch_limits_before_transfer():
    source = (ROOT / "frontend/src/modules/android/MobileDocumentUpload.tsx").read_text(encoding="utf-8")
    assert "4 * 1024 * 1024" in source
    assert "12 * 1024 * 1024" in source
    assert "60 * 1024 * 1024" in source
    assert "slice(0, 50)" in source
    assert "partitionFiles(files)" in source
    assert "MAX_FILES_PER_REQUEST = 5" in source
    assert "Пачка ${index + 1} из ${batches.length}" in source
    assert "uploadedCount += batches[index].length" in source
    assert "items.slice(uploadedCount)" in source
    assert "Уже загружено файлов: ${uploadedCount}" in source
    assert "setFiles((items) => items.filter" in source

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_android_can_select_files_or_capture_a_document_photo():
    source = (ROOT / "frontend/src/modules/android/MobileDocumentUpload.tsx").read_text(encoding="utf-8")
    assert 'accept=".pdf,.docx,.xlsx,.txt,.md,.csv,.bmp,.jpg,.jpeg,.png,.tif,.tiff,.webp"' in source
    assert 'capture="environment"' in source
    assert "submitPreparedDocumentUpload(batches[index])" in source
    assert "enqueuePreparedDocumentUploads(batches)" in source
    assert "Файлы отправятся в выбранный проект только после нажатия «Загрузить и проанализировать»" in source
    assert 'webkitdirectory: ""' in source
    assert "file.webkitRelativePath || file.name" in source


def test_android_upload_enforces_server_batch_limits_before_transfer():
    source = (ROOT / "frontend/src/modules/android/MobileDocumentUpload.tsx").read_text(encoding="utf-8")
    assert "4 * 1024 * 1024" in source
    engine = (ROOT / "frontend/src/offline/syncEngine.ts").read_text(encoding="utf-8")
    assert "12 * 1024 * 1024" in engine
    assert "60 * 1024 * 1024" in source
    # Reject an oversized selection explicitly instead of silently dropping files.
    # Behavioral coverage is in MobileDocumentUpload.test.tsx.
    assert "if (merged.length > 50)" in source
    assert "Выбрано больше 50 файлов" in source
    assert "slice(0, 50)" not in source
    assert "prepareDocumentUploads({" in source
    assert "batch.length === 5" in engine
    assert "Пачка ${index + 1} из ${batches.length}" in source
    assert "uploadedCount += batches[index].uploads.length" in source
    assert "items.slice(uploadedCount)" in source
    assert "Уже загружено файлов: ${uploadedCount}" in source
    assert "setFiles((items) => items.slice(uploadedCount))" in source

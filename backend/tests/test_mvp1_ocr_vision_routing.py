from app.integrations.contracts import AdapterHealth
from app.api.workspace import _populate_content
from app.ocr_quality.routing import ExtractionPolicy, capabilities, route_extraction
from app.organizer_engine.types import DriveFile


class Vision:
    provider = "synthetic"
    calls = 0
    def health(self): return AdapterHealth(True, "synthetic")
    def analyze_document(self, text, filename): raise AssertionError
    def analyze_message(self, text, context_name): raise AssertionError
    def analyze_document_image(self, **kwargs):
        self.calls += 1
        assert set(kwargs) == {"data", "mime_type", "filename"}
        return {"text": "Распознанный договор № 17 от 01.09.2026", "confidence": 0.94}


def test_local_mode_never_sends_document_to_external_adapter():
    adapter = Vision()
    result = route_extraction(b"", "image/png", "scan.png", mode="ocr",
                              policy=ExtractionPolicy(False, True), adapter=adapter)
    assert adapter.calls == 0 and result.external_bytes_sent is False
    assert result.incomplete_reason in {"empty_result", "low_confidence", "local_ocr_unavailable"}


def test_requested_vision_is_explicitly_incomplete_when_policy_disables_egress():
    adapter = Vision()
    result = route_extraction(b"", "image/png", "scan.png", mode="vision",
                              policy=ExtractionPolicy(False, True), adapter=adapter)
    assert result.incomplete_reason == "external_vision_disabled"
    assert capabilities(ExtractionPolicy(False, True), adapter)["external_vision"] is False
    assert adapter.calls == 0


def test_explicit_external_policy_routes_only_through_ai_provider_adapter():
    adapter = Vision()
    result = route_extraction(b"synthetic image", "image/png", "scan.png", mode="both",
        policy=ExtractionPolicy(allow_external_vision=True, local_only=False), adapter=adapter)
    assert adapter.calls == 1 and result.external_bytes_sent is True
    assert result.used_modes[-1] == "external_vision"
    assert result.result.method == "ocr+vision" and result.incomplete_reason is None


def test_unavailable_external_capability_fails_closed_with_reason():
    class NoVision(Vision):
        analyze_document_image = None
    result = route_extraction(b"", "application/pdf", "scan.pdf", mode="vision",
        policy=ExtractionPolicy(True, False), adapter=NoVision())
    assert result.incomplete_reason == "external_vision_unavailable"
    assert result.external_bytes_sent is False


def test_mvp1_snapshot_pipeline_uses_router_and_preserves_incomplete_reason():
    class LocalStorage:
        def read_bytes(self, object_id, max_bytes):
            assert object_id == "scan" and max_bytes == 4 * 1024 * 1024
            return b"", "image/png"

    item = DriveFile("scan", "scan.png", "image/png", "root")
    extracted, failed = _populate_content(LocalStorage(), [item])
    assert (extracted, failed) == (0, 0)
    assert item.provider_metadata["extraction_method"] in {"native", "ocr", "unsupported"}
    assert item.provider_metadata["extraction_incomplete_reason"] in {"empty_result", "low_confidence"}

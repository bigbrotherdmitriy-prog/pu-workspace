"""Provider-neutral OCR/vision policy router for MVP-1.

Local extraction is always the baseline.  External bytes are passed only to an
explicitly enabled AIProviderAdapter capability; unsupported or low-confidence
paths return an incomplete reason and require human review.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from app.integrations.contracts import AIProviderAdapter
from app.organizer_engine.content import ExtractionResult, OCR_REVIEW_CONFIDENCE, extract_text_result


Mode = Literal["auto", "ocr", "vision", "both"]
IncompleteReason = Literal[
    "external_vision_disabled", "external_vision_unavailable", "local_ocr_unavailable",
    "low_confidence", "empty_result", "page_limit",
]


@dataclass(frozen=True, slots=True)
class ExtractionPolicy:
    allow_external_vision: bool = False
    local_only: bool = True


@dataclass(frozen=True, slots=True)
class RoutedExtraction:
    result: ExtractionResult
    requested_mode: Mode
    used_modes: tuple[str, ...]
    incomplete_reason: IncompleteReason | None
    external_bytes_sent: bool


def capabilities(policy: ExtractionPolicy, adapter: AIProviderAdapter | None = None) -> dict[str, bool]:
    external = bool(
        policy.allow_external_vision and not policy.local_only and adapter is not None
        and callable(getattr(adapter, "analyze_document_image", None)) and adapter.health().ready
    )
    return {"local_ocr": True, "external_vision": external, "combined": external}


def route_extraction(data: bytes, mime_type: str, filename: str, *, mode: Mode = "auto",
                     policy: ExtractionPolicy = ExtractionPolicy(),
                     adapter: AIProviderAdapter | None = None) -> RoutedExtraction:
    if mode not in {"auto", "ocr", "vision", "both"}:
        raise ValueError("unsupported_extraction_mode")
    local = extract_text_result(data, mime_type, filename)
    used = ["native" if local.method == "native" else "local_ocr"]
    wants_vision = mode in {"vision", "both"}
    if not wants_vision:
        reason: IncompleteReason | None = None
        if not local.text.strip(): reason = "empty_result"
        elif local.needs_review or local.confidence < OCR_REVIEW_CONFIDENCE: reason = "low_confidence"
        elif any(value.startswith("ocr_page_limit:") for value in local.warnings): reason = "page_limit"
        return RoutedExtraction(local, mode, tuple(used), reason, False)

    caps = capabilities(policy, adapter)
    if not policy.allow_external_vision or policy.local_only:
        return RoutedExtraction(local, mode, tuple(used), "external_vision_disabled", False)
    if not caps["external_vision"] or adapter is None:
        return RoutedExtraction(local, mode, tuple(used), "external_vision_unavailable", False)
    vision_method = getattr(adapter, "analyze_document_image")
    vision = vision_method(data=data, mime_type=mime_type, filename=filename)
    if not isinstance(vision, dict) or not isinstance(vision.get("text"), str):
        return RoutedExtraction(local, mode, tuple(used), "external_vision_unavailable", True)
    confidence = vision.get("confidence", 0.0)
    if type(confidence) not in {int, float} or not 0 <= float(confidence) <= 1:
        return RoutedExtraction(local, mode, tuple(used), "external_vision_unavailable", True)
    text = vision["text"].strip()
    choose_vision = bool(text) and (mode == "vision" or len(text) > len(local.text.strip()))
    if choose_vision:
        local.text = text
        local.method = "vision" if mode == "vision" else "ocr+vision"
        local.confidence = float(confidence)
        local.quality = "high" if confidence >= 0.85 else "medium" if confidence >= 0.6 else "low"
    used.append("external_vision")
    local.needs_review = local.confidence < OCR_REVIEW_CONFIDENCE
    reason = "empty_result" if not local.text else "low_confidence" if local.needs_review else None
    return RoutedExtraction(local, mode, tuple(used), cast(IncompleteReason | None, reason), True)

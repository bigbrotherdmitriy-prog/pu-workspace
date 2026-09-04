from __future__ import annotations

from typing import Any
import os

from app.gemini_analysis import analyze_document_with_gemini, analyze_message_with_gemini, gemini_configured
from app.integrations.contracts import AdapterHealth


class GeminiAIAdapter:
    """Current external AI implementation behind the provider-neutral contract."""

    provider = "gemini"

    @property
    def model(self) -> str:
        return os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

    def health(self) -> AdapterHealth:
        ready = gemini_configured()
        return AdapterHealth(
            ready=ready,
            detail=(
                f"configured for {self.model}; connectivity is verified on each analysis request"
                if ready else "api key is not configured"
            ),
        )

    def analyze_document(self, text: str, filename: str) -> dict[str, Any]:
        return analyze_document_with_gemini(text, filename)

    def analyze_message(self, text: str, context_name: str) -> dict[str, Any]:
        return analyze_message_with_gemini(text, context_name)


def configured_ai_provider() -> GeminiAIAdapter:
    # Vertical Slice 1 intentionally keeps one implementation. Selection remains
    # outside the core so another provider can be added without changing engines.
    return GeminiAIAdapter()

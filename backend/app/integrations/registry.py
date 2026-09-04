from __future__ import annotations

from typing import TypeVar

from app.integrations.contracts import IntegrationAdapter

T = TypeVar("T", bound=IntegrationAdapter)


class AdapterRegistry:
    """Small explicit registry; application core resolves capabilities, not vendors."""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], IntegrationAdapter] = {}

    def register(self, capability: str, adapter: T) -> T:
        key = (capability.strip().lower(), adapter.provider.strip().lower())
        if not all(key):
            raise ValueError("Adapter capability and provider are required")
        self._adapters[key] = adapter
        return adapter

    def get(self, capability: str, provider: str) -> IntegrationAdapter:
        key = (capability.strip().lower(), provider.strip().lower())
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise LookupError(f"Adapter is not registered: {key[0]}/{key[1]}") from exc

    def providers(self, capability: str) -> tuple[str, ...]:
        normalized = capability.strip().lower()
        return tuple(sorted(provider for cap, provider in self._adapters if cap == normalized))

"""Opt-in synthetic communication facades; no routes or execution wiring."""

from .service import ContextCommunication, ContextError

__all__ = ["ContextCommunication", "ContextError"]

"""Versioned, deterministic MVP-1 classification precedence.

The evaluator selects a signal; it never mutates storage and never treats an
AI answer as a policy rule.  All callers therefore share the exact order from
the specification instead of reconstructing it ad hoc.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping

from .config import DEFAULT_FOLDER, FOLDER_STRUCTURE, MIN_AUTO_CONFIDENCE
from .types import Classification


RULE_PRECEDENCE_VERSION = "mvp1.classification-precedence.1"
RuleLayer = Literal["policy", "project", "organization"]
SignalSource = Literal["policy", "project", "organization", "manual", "metadata", "ai", "default"]
_VALID_FOLDERS = frozenset(folder for folder, _ in FOLDER_STRUCTURE)


@dataclass(frozen=True, slots=True)
class ClassificationSignal:
    folder: str
    confidence: float
    reasoning: str
    source: SignalSource


@dataclass(frozen=True, slots=True)
class PrecedenceDecision:
    version: str
    classification: Classification
    source: SignalSource


def _rule_signal(filename: str, rules: Iterable[Mapping], source: RuleLayer) -> ClassificationSignal | None:
    folded = filename.casefold()
    for rule in rules:
        pattern = rule.get("pattern") if isinstance(rule, Mapping) else None
        action = rule.get("action") if isinstance(rule, Mapping) else None
        keyword = pattern.get("filename_contains") if isinstance(pattern, Mapping) else None
        folder = action.get("folder") if isinstance(action, Mapping) else None
        if (isinstance(keyword, str) and keyword and folder in _VALID_FOLDERS
                and keyword.casefold() in folded):
            return ClassificationSignal(folder, 1.0, f"{source} rule matched", source)
    return None


def evaluate_precedence(
    *, filename: str, policy_rules: Iterable[Mapping] = (), project_rules: Iterable[Mapping] = (),
    organization_rules: Iterable[Mapping] = (), manual: ClassificationSignal | None = None,
    metadata: ClassificationSignal | None = None, ai: ClassificationSignal | None = None,
) -> PrecedenceDecision:
    """Apply policy→project→organization→manual→metadata→AI→default."""
    candidates = (
        _rule_signal(filename, policy_rules, "policy"),
        _rule_signal(filename, project_rules, "project"),
        _rule_signal(filename, organization_rules, "organization"),
        manual,
        metadata,
        ai,
    )
    for signal in candidates:
        if signal is None:
            continue
        if signal.folder not in _VALID_FOLDERS or not 0 <= signal.confidence <= 1:
            continue
        ambiguous = signal.source == "ai" and signal.confidence < MIN_AUTO_CONFIDENCE
        return PrecedenceDecision(
            RULE_PRECEDENCE_VERSION,
            Classification(signal.folder, signal.confidence, signal.reasoning, ambiguous),
            signal.source,
        )
    return PrecedenceDecision(
        RULE_PRECEDENCE_VERSION,
        Classification(DEFAULT_FOLDER, 0.0, "No classification signal matched", True),
        "default",
    )

"""Shared LLM-based extraction for task_engine/response_engine/governance_engine.

One combined Gemini call per file replaces the LLM-relevant work of all three
engines at once (obligations incl. due date/assignee/amount, response-worthy
requests, risks, decisions) -- not three separate calls -- per
docs/audits/mvp2-ai-extraction-preflight.md §9-10.

Fallback (Вариант А, confirmed 2026-09-12): the existing regex engines are
never removed. On any LLM failure -- policy block, provider not configured,
temporary error, or an invalid/unusable response -- поручение/срок fall back
to today's regex extraction (task_engine.extract_task_candidates), and
response/risk/decision candidates fall back to their own existing regex
engines unchanged. Ответственный/сумма have no regex equivalent and simply
stay empty with needs_review, as they do today when nothing extracts them.

The three engines call `extract_for_file` once per file; a transient,
per-instance cache on StorageObject (see app/core/integration_types.py)
means the first of the three calls in a batch does the real work (LLM or
regex) and the other two reuse its result -- no orchestration call site
(organizer.py, ai_secretary.py, etc.) needs to change.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_policy import ExternalAIBlocked, apply_ai_policy_mode, policy_mode_for_project
from app.core.integration_types import StorageObject
from app.integrations.ai import configured_ai_provider
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User

# Enum→float mapping (confirmed 2026-09-12): 0.45 already exists in the
# codebase as a review-worthy floor (ai_secretary._completion_candidate_score),
# not a new magic number.
CONFIDENCE_FROM_LLM_LEVEL = {"low": 0.45, "medium": 0.75, "high": 0.92}

# Regex prefilter (Вариант Г): reuse the engines' own detectors purely as an
# existence check across the whole file, not per-sentence extraction. A file
# that matches none of these almost certainly has nothing for any of the four
# categories, so it never reaches the LLM at all.
_PREFILTER_PATTERNS = None  # populated lazily; see _prefilter_patterns()


def _prefilter_patterns():
    global _PREFILTER_PATTERNS
    if _PREFILTER_PATTERNS is None:
        # Deferred import: task_engine/response_engine/governance_engine call
        # back into this module, so this module must not import them at load
        # time -- only their already-compiled regex constants, lazily.
        from app.task_engine import IMPERATIVE_RE, OBLIGATION_RE
        from app.response_engine import REQUEST_RE
        from app.governance_engine import DECISION_RE, RISK_RE
        _PREFILTER_PATTERNS = (OBLIGATION_RE, IMPERATIVE_RE, REQUEST_RE, RISK_RE, DECISION_RE)
    return _PREFILTER_PATTERNS


def _looks_actionable(text: str) -> bool:
    if not text or not text.strip():
        return False
    if "?" in text:
        return True
    return any(pattern.search(text) for pattern in _prefilter_patterns())


@dataclass(slots=True)
class ObligationCandidate:
    """Candidate score is a heuristic review signal, not a calibrated probability.

    Superset of task_engine.TaskCandidate: the regex fallback path converts
    each TaskCandidate into one of these (extraction_method="regex", the new
    fields left null); the LLM path builds these directly from Gemini's
    response.
    """

    title: str
    excerpt: str
    due_date: date | None
    due_date_evidence_quote: str | None
    assignee_hint: str | None
    assignee_evidence_quote: str | None
    amount: Decimal | None
    amount_currency: str | None
    amount_evidence_quote: str | None
    confidence: float
    extraction_method: str  # "llm" | "regex"
    priority: str = "normal"
    review_reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class RawResponseCandidate:
    evidence_quote: str
    confidence: float


@dataclass(slots=True)
class RawRiskCandidate:
    title: str
    evidence_quote: str
    kind: str
    criticality: str
    confidence: float


@dataclass(slots=True)
class RawDecisionCandidate:
    question: str
    evidence_quote: str
    confidence: float


@dataclass(slots=True)
class CombinedExtraction:
    obligations: list[ObligationCandidate] = field(default_factory=list)
    response_candidates: list[RawResponseCandidate] = field(default_factory=list)
    risks: list[RawRiskCandidate] = field(default_factory=list)
    decisions: list[RawDecisionCandidate] = field(default_factory=list)
    extraction_method: str = "empty"  # "llm" | "regex" | "empty"
    fallback_reason: str | None = None


# Human-readable text for CombinedExtraction.fallback_reason, shared across
# the three engines so a Task/Draft/Risk description reads consistently
# regardless of which one created it.
FALLBACK_REASON_TEXT = {
    "policy_blocked": "AI отключён политикой проекта",
    "not_configured": "AI не настроен",
    "temporarily_unavailable": "AI временно недоступен",
    "invalid_response": "AI вернул некорректный ответ",
}


class LLMExtractionFailed(RuntimeError):
    """Carries one of the four classified reasons (Вариант В)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


_MAX_QUOTE_LENGTH = 2000


def _verbatim(quote: str | None, original_text: str) -> str | None:
    """Return quote unchanged if it is a real substring of the source, else None."""
    if not quote:
        return None
    quote = quote[:_MAX_QUOTE_LENGTH]
    if quote.casefold() not in original_text.casefold():
        return None
    return quote


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_amount(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _member_names(db: Session, project_id: int) -> list[str]:
    return list(db.scalars(
        select(User.name).join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
    ))


def match_assignee_hint(db: Session, project_id: int, hint: str | None) -> User | None:
    """Deterministic, non-LLM matching of a text hint to a real project member.

    Substring match against member display names, case-insensitive; ties
    broken by role (owner > manager > editor > member > viewer), same order
    as task_engine._default_assignee's fallback.
    """
    if not hint or not hint.strip():
        return None
    role_order = {"owner": 0, "manager": 1, "editor": 2, "member": 3, "viewer": 4}
    rows = db.execute(
        select(User, ProjectMember.role).join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
    ).all()
    needle = hint.strip().casefold()
    matches = [row for row in rows if needle in row.User.name.casefold() or row.User.name.casefold() in needle]
    if not matches:
        return None
    matches.sort(key=lambda row: (role_order.get(row.role, 9), row.User.id))
    return matches[0].User


def _obligations_from_llm(payload: list[dict], text: str) -> list[ObligationCandidate]:
    result: list[ObligationCandidate] = []
    for item in payload:
        title = str(item.get("title") or "").strip()[:500]
        evidence_quote = _verbatim(item.get("evidence_quote"), text)
        if not title or not evidence_quote:
            continue  # cannot trust an obligation whose core evidence doesn't verify
        confidence = CONFIDENCE_FROM_LLM_LEVEL.get(item.get("confidence"), 0.45)

        due_date_quote = _verbatim(item.get("due_date_evidence_quote"), text)
        due_date = _parse_iso_date(item.get("due_date")) if due_date_quote else None

        assignee_quote = _verbatim(item.get("assignee_evidence_quote"), text)
        assignee_hint = str(item.get("assignee_hint") or "").strip()[:300] if assignee_quote else None
        assignee_hint = assignee_hint or None

        amount_quote = _verbatim(item.get("amount_evidence_quote"), text)
        amount = _parse_amount(item.get("amount")) if amount_quote else None
        amount_currency = (str(item.get("amount_currency") or "").strip()[:8] or None) if amount_quote else None

        result.append(ObligationCandidate(
            title=title, excerpt=evidence_quote, due_date=due_date,
            due_date_evidence_quote=due_date_quote if due_date else None,
            assignee_hint=assignee_hint, assignee_evidence_quote=assignee_quote if assignee_hint else None,
            amount=amount, amount_currency=amount_currency,
            amount_evidence_quote=amount_quote if amount is not None else None,
            confidence=confidence, extraction_method="llm",
        ))
    return result


def _responses_from_llm(payload: list[dict], text: str) -> list[RawResponseCandidate]:
    result = []
    for item in payload:
        quote = _verbatim(item.get("evidence_quote"), text)
        if not quote:
            continue
        result.append(RawResponseCandidate(
            evidence_quote=quote,
            confidence=CONFIDENCE_FROM_LLM_LEVEL.get(item.get("confidence"), 0.45),
        ))
    return result


def _risks_from_llm(payload: list[dict], text: str) -> list[RawRiskCandidate]:
    result = []
    for item in payload:
        quote = _verbatim(item.get("evidence_quote"), text)
        title = str(item.get("title") or "").strip()[:240]
        if not quote or not title:
            continue
        kind = item.get("kind") if item.get("kind") in ("risk", "deviation") else "risk"
        criticality = item.get("criticality") if item.get("criticality") in ("medium", "high") else "medium"
        result.append(RawRiskCandidate(
            title=title, evidence_quote=quote, kind=kind, criticality=criticality,
            confidence=CONFIDENCE_FROM_LLM_LEVEL.get(item.get("confidence"), 0.45),
        ))
    return result


def _decisions_from_llm(payload: list[dict], text: str) -> list[RawDecisionCandidate]:
    result = []
    for item in payload:
        quote = _verbatim(item.get("evidence_quote"), text)
        question = str(item.get("question") or "").strip()
        if not quote or not question:
            continue
        result.append(RawDecisionCandidate(
            question=question, evidence_quote=quote,
            confidence=CONFIDENCE_FROM_LLM_LEVEL.get(item.get("confidence"), 0.45),
        ))
    return result


def _regex_fallback(text: str, filename: str, *, reason: str | None) -> CombinedExtraction:
    # Deferred imports -- see _prefilter_patterns() for why.
    from app.task_engine import extract_task_candidates
    from app.response_engine import extract_response_candidates
    from app.governance_engine import extract_governance_candidates

    obligations = [
        ObligationCandidate(
            title=candidate.title, excerpt=candidate.excerpt, due_date=candidate.due_date,
            due_date_evidence_quote=candidate.excerpt if candidate.due_date else None,
            assignee_hint=None, assignee_evidence_quote=None,
            amount=None, amount_currency=None, amount_evidence_quote=None,
            confidence=candidate.confidence, extraction_method="regex",
            priority=candidate.priority, review_reasons=candidate.review_reasons,
        )
        for candidate in extract_task_candidates(text)
    ]
    # ensure_response's "still produce a generic draft" bonus is not a regex
    # concern -- it applies identically whichever path found the (empty) list
    # of response candidates, so it lives in response_engine.create_response_drafts,
    # applied once to whatever extract_for_file/_files returns.
    responses = [
        RawResponseCandidate(evidence_quote=candidate.excerpt, confidence=candidate.confidence)
        for candidate in extract_response_candidates(text, filename)
    ]
    risk_candidates, decision_candidates = extract_governance_candidates(text)
    risks = [
        RawRiskCandidate(
            title=candidate["text"][:240], evidence_quote=candidate["text"],
            kind=candidate["kind"], criticality=candidate["criticality"], confidence=0.82,
        )
        for candidate in risk_candidates
    ]
    decisions = [
        RawDecisionCandidate(question=candidate["text"], evidence_quote=candidate["text"], confidence=0.80)
        for candidate in decision_candidates
    ]
    return CombinedExtraction(
        obligations=obligations, response_candidates=responses, risks=risks, decisions=decisions,
        extraction_method="regex", fallback_reason=reason,
    )


@dataclass(slots=True, frozen=True)
class _BatchContext:
    """Everything the LLM call needs, gathered with db access once per batch/project --
    never per file, and never touched again once dispatched to a worker thread.
    """

    project_name: str
    member_names: tuple[str, ...]
    policy_mode: str


def _batch_context(db: Session, project_id: int) -> _BatchContext:
    project = db.get(Project, project_id)
    return _BatchContext(
        project_name=project.name if project else "",
        member_names=tuple(_member_names(db, project_id)),
        policy_mode=policy_mode_for_project(db, project_id),
    )


def _call_llm(context: _BatchContext, text: str, filename: str) -> dict:
    """Pure w.r.t. the database -- safe to run inside a worker thread.

    Raises LLMExtractionFailed with one of Вариант В's four reasons, or
    returns the parsed dict.
    """
    try:
        ai_text = apply_ai_policy_mode(context.policy_mode, text)
    except ExternalAIBlocked as exc:
        raise LLMExtractionFailed("policy_blocked", str(exc)) from exc
    provider = configured_ai_provider()
    if not provider.health().ready:
        raise LLMExtractionFailed("not_configured")
    try:
        result = provider.extract_fields(ai_text, filename, context.project_name, list(context.member_names))
    except Exception as exc:  # noqa: BLE001 -- any provider/HTTP failure is "temporarily unavailable"
        raise LLMExtractionFailed("temporarily_unavailable", str(exc)) from exc
    if not isinstance(result, dict) or "obligations" not in result:
        raise LLMExtractionFailed("invalid_response")
    return result


def _extract_with_context(context: _BatchContext, text: str | None, filename: str) -> CombinedExtraction:
    """No db access at all -- the only function actually run inside worker threads."""
    if not text or not text.strip():
        return CombinedExtraction()
    if not _looks_actionable(text):
        return CombinedExtraction()  # prefilter: nothing for any of the four categories
    if context.policy_mode == "local_only":
        # Permanent, not a fallback-on-failure case (§4.0): never even attempt the call.
        return _regex_fallback(text, filename, reason="policy_blocked")
    try:
        payload = _call_llm(context, text, filename)
    except LLMExtractionFailed as exc:
        return _regex_fallback(text, filename, reason=exc.reason)
    return CombinedExtraction(
        obligations=_obligations_from_llm(payload.get("obligations") or [], text),
        response_candidates=_responses_from_llm(payload.get("response_candidates") or [], text),
        risks=_risks_from_llm(payload.get("risks") or [], text),
        decisions=_decisions_from_llm(payload.get("decisions") or [], text),
        extraction_method="llm",
    )


def extract_for_text(db: Session, project_id: int, text: str | None, filename: str) -> CombinedExtraction:
    """Single-file/-text entry point (also used directly by tests). Fetches its own
    batch context -- for more than one file in a project, prefer extract_for_files."""
    context = _batch_context(db, project_id)
    return _extract_with_context(context, text, filename)


def extract_for_file(db: Session, file: StorageObject, project_id: int) -> CombinedExtraction:
    cached = file.llm_extraction_cache
    if cached is not None:
        return cached
    result = extract_for_text(db, project_id, file.content_text, file.name)
    file.llm_extraction_cache = result
    return result


def extract_for_files(db: Session, files: list[StorageObject], project_id: int) -> None:
    """Populate llm_extraction_cache on every file (Вариант Б: bounded worker pool for bulk).

    Call once per batch -- the first of the three engine functions
    (create_tasks_from_files/create_response_drafts/create_governance_items)
    to run at any of the ~9 call sites should call this; the other two find
    every file already cached and make no further LLM calls.

    A single file's LLM failure does not abort the batch: extract_for_text's
    own fallback (Вариант А) applies per file, independently.
    """
    pending = [f for f in files if not f.is_folder and f.llm_extraction_cache is None]
    if not pending:
        return
    context = _batch_context(db, project_id)
    if len(pending) == 1:
        pending[0].llm_extraction_cache = _extract_with_context(
            context, pending[0].content_text, pending[0].name,
        )
        return
    workers = max(1, min(int(os.getenv("LLM_EXTRACTION_CONCURRENCY", "4")), len(pending)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_file = {
            executor.submit(_extract_with_context, context, file.content_text, file.name): file
            for file in pending
        }
        for future in as_completed(future_to_file):
            future_to_file[future].llm_extraction_cache = future.result()

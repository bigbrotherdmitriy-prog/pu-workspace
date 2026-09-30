import re

from app.api import execution_finance as finance
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.organization_contract import Organization
from app.models.project import Project


def test_document_candidates_preserve_ranking_and_only_extract_visible_hints(db_session, user_factory, monkeypatch):
    db = db_session
    user = user_factory(is_admin=True)
    organization = Organization(name="Candidate performance")
    db.add(organization); db.flush()
    project = Project(name="Selected", organization_id=organization.id)
    other = Project(name="Other", organization_id=organization.id)
    db.add_all([project, other]); db.flush()
    for i in range(120):
        document = Document(project_id=project.id, name=f"Счет {i:03} на оплату.pdf", source="local_upload")
        db.add(document); db.flush()
        db.add(DocumentVersion(document_id=document.id, version_number=1,
            content="СЧЕТ\tНА\u00a0ОПЛАТУ № 57 от 28.08.2026. Итого к оплате 125 400,50 руб."))
    db.add(Document(project_id=other.id, name="Счет другого проекта.pdf"))
    db.commit()
    original_hints = finance._finance_document_hints
    hint_calls = []
    def hints(name, content):
        hint_calls.append(name)
        return original_hints(name, content)
    monkeypatch.setattr(finance, "_finance_document_hints", hints)
    result = finance.document_candidates(project.id, db=db, user=user)
    assert len(result["candidates"]) == len(hint_calls) == 100
    assert [row["name"] for row in result["candidates"]] == [f"Счет {i:03} на оплату.pdf" for i in range(100)]
    for i, row in enumerate(result["candidates"]):
        assert row["hints"] == {"amount": "125400.50", "date": "2026-08-28", "number": f"{i:03}"}
    assert result["originals_changed"] is False


def test_normalized_classifier_matches_original_whitespace_rules():
    for name, content in [
        ("\tСЧЕТ_на\u00a0оплату.pdf ", "\nИтого 120\tруб."),
        ("Акт.pdf", " \tАкт\nвыполненных\u00a0работ  "),
        ("График.doc", "Календарный\nплан"),
        ("Договор.doc", "x" * 120_000 + "СЧЕТ НА ОПЛАТУ"),
    ]:
        normalized_name = re.sub(r"\s+", " ", name.casefold().replace("_", " "))
        normalized_text = re.sub(r"\s+", " ", content[:120_000].casefold())
        for kind in finance._DOCUMENT_KIND_MARKERS:
            assert finance._finance_document_score(name, content, kind) == finance._finance_document_score_normalized(
                normalized_name, normalized_text, kind)

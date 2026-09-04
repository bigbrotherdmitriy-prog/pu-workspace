from app.response_engine import extract_response_candidates


def test_explicit_request_creates_grounded_draft():
    drafts = extract_response_candidates("Просим предоставить акт выполненных работ.", "Письмо.docx")
    assert len(drafts) == 1
    assert "Просим предоставить акт" in drafts[0].body
    assert drafts[0].confidence == 0.80


def test_plain_statement_does_not_create_draft():
    assert extract_response_candidates("Договор подписан сторонами.", "Договор.pdf") == []


def test_email_mode_creates_safe_reviewable_draft_without_explicit_request():
    drafts = extract_response_candidates(
        "Направляем подписанный акт выполненных работ во вложении.",
        "Заказчик — Акт выполненных работ",
        ensure_response=True,
    )
    assert len(drafts) == 1
    assert "Информация получена" in drafts[0].body
    assert drafts[0].confidence == 0.55


def test_email_mode_ignores_empty_or_trivial_content():
    assert extract_response_candidates("Спасибо", "Письмо", ensure_response=True) == []

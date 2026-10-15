from datetime import date
from decimal import Decimal
from email.message import EmailMessage

from sqlalchemy import select

from app import mail_invoice_intake
from app.invoice_extraction import InvoiceFields
from app.models.document import Document
from app.models.management import Notification
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db_session, user_factory):
    user = user_factory()
    organization = Organization(name="Mail invoice test")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Project MI", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    db_session.commit()
    return user, project


def _fields():
    return InvoiceFields(
        amount=Decimal("120000.00"), amount_evidence_quote="120 000 ₽", currency="RUB",
        counterparty="ООО «Ромашка»", counterparty_evidence_quote="ООО «Ромашка»",
        payment_purpose=None, payment_purpose_evidence_quote=None,
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=date(2024, 1, 1), confidence=0.9, extraction_method="llm",
    )


def _mail(subject: str, filename: str, data: bytes = b"%PDF-1.4 invoice") -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "Sender <sender@example.test>"
    message.set_content("body")
    message.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
    return message


def _patch(monkeypatch, *, text: str = "Счет на оплату 120 000 руб."):
    monkeypatch.setattr("app.organizer_engine.content.extract_text", lambda *a, **k: text)
    monkeypatch.setattr("app.invoice_extraction.extract_invoice_fields", lambda *a, **k: _fields())


def test_invoice_attachment_creates_proposal_document_and_notification(db_session, user_factory, monkeypatch):
    user, project = _world(db_session, user_factory)
    _patch(monkeypatch)

    created = mail_invoice_intake.intake_invoice_attachments(
        db_session, project_id=project.id, message_external_id="msg-1",
        subject="Счёт", parsed=_mail("Счёт 15", "Счет 15.pdf"), user_id=user.id,
    )
    db_session.commit()

    assert len(created) == 1
    document = db_session.scalar(select(Document).where(
        Document.project_id == project.id, Document.source == "yandex_mail",
    ))
    assert document is not None
    notification = db_session.scalar(select(Notification).where(
        Notification.kind == "invoice_from_mail",
    ))
    assert notification is not None
    assert "120 000" in notification.body


def test_repeat_intake_is_idempotent(db_session, user_factory, monkeypatch):
    user, project = _world(db_session, user_factory)
    _patch(monkeypatch)
    parsed = _mail("Счёт 15", "Счет 15.pdf")
    mail_invoice_intake.intake_invoice_attachments(
        db_session, project_id=project.id, message_external_id="msg-2",
        subject="Счёт", parsed=parsed, user_id=user.id,
    )
    db_session.commit()

    again = mail_invoice_intake.intake_invoice_attachments(
        db_session, project_id=project.id, message_external_id="msg-2",
        subject="Счёт", parsed=parsed, user_id=user.id,
    )
    db_session.commit()

    assert again == []


def test_non_invoice_attachments_are_skipped(db_session, user_factory, monkeypatch):
    user, project = _world(db_session, user_factory)
    _patch(monkeypatch)

    photo = _mail("Привет", "фото.jpg")
    act = _mail("Привет", "Акт.pdf")

    assert mail_invoice_intake.intake_invoice_attachments(
        db_session, project_id=project.id, message_external_id="msg-3",
        subject="Привет", parsed=photo, user_id=user.id,
    ) == []
    assert mail_invoice_intake.intake_invoice_attachments(
        db_session, project_id=project.id, message_external_id="msg-4",
        subject="Привет", parsed=act, user_id=user.id,
    ) == []


def test_extract_text_failure_does_not_raise(db_session, user_factory, monkeypatch):
    user, project = _world(db_session, user_factory)
    monkeypatch.setattr("app.organizer_engine.content.extract_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("app.invoice_extraction.extract_invoice_fields", lambda *a, **k: _fields())

    result = mail_invoice_intake.intake_invoice_attachments(
        db_session, project_id=project.id, message_external_id="msg-5",
        subject="Счёт", parsed=_mail("Счёт 15", "Счет 15.pdf"), user_id=user.id,
    )

    assert result == []

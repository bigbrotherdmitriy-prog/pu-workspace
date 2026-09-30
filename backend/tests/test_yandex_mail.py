from email.message import EmailMessage

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.api import yandex_mail
from app.core.token_crypto import decrypt_token, encrypt_token
from app.integrations.catalog import project_integration_catalog
from app.models.integration_credential import IntegrationCredential
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


class FakeImap:
    def __init__(self, messages=None):
        self.messages = messages or {}
        self.calls = []

    def login(self, email, password):
        self.calls.append(("login", email, password))
        return "OK", [b"authenticated"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        self.calls.append(("response", name))
        return "UIDVALIDITY", [b"321"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in self.messages)]
        if command == "fetch":
            uid = int(args[0])
            return "OK", [(b"1 (RFC822", self.messages[uid]), b")"]
        raise AssertionError(command)

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b"done"]


def _world(db_session, user_factory):
    user = user_factory()
    organization = Organization(name="Yandex Mail test")
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Project 17", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    db_session.commit()
    return user, project


def _mail() -> bytes:
    message = EmailMessage()
    message["Subject"] = "Test subject"
    message["From"] = "Sender <sender@example.test>"
    message["To"] = "mailbox@example.test"
    message["Message-ID"] = "<test-1@example.test>"
    message.set_content("Plain body")
    message.add_attachment(b"attachment", maintype="application", subtype="octet-stream", filename="offer.bin")
    return message.as_bytes()


def test_connect_verifies_readonly_inbox_and_encrypts_password(db_session, user_factory, monkeypatch):
    user, project = _world(db_session, user_factory)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    client = FakeImap()
    monkeypatch.setattr(yandex_mail, "_new_imap", lambda: client)

    result = yandex_mail.connect_yandex_mail(
        project.id,
        yandex_mail.YandexMailConnection(email=" MAILBOX@EXAMPLE.TEST ", app_password="app-password-123"),
        db_session,
        user,
    )

    credential = db_session.scalar(select(IntegrationCredential).where(
        IntegrationCredential.project_id == project.id,
        IntegrationCredential.provider == "yandex_mail",
    ))
    assert result == {"connected": True, "provider": "yandex_mail", "account_email": "mailbox@example.test", "read_only": True}
    assert credential.access_token != "app-password-123"
    assert decrypt_token(credential.access_token) == "app-password-123"
    assert ("select", "INBOX", True) in client.calls


def test_sync_uses_body_peek_and_deduplicates(db_session, user_factory, monkeypatch):
    user, project = _world(db_session, user_factory)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    db_session.add(IntegrationCredential(
        project_id=project.id,
        provider="yandex_mail",
        capability="channel",
        access_token=encrypt_token("app-password-123"),
        account_email="mailbox@example.test",
        scopes="imap.readonly",
    ))
    db_session.commit()
    client = FakeImap({10: _mail()})
    monkeypatch.setattr(yandex_mail, "_new_imap", lambda: client)
    ingested = []

    def fake_ingest(payload, _db, _user):
        ingested.append(payload)
        return {"id": 999999}

    monkeypatch.setattr(yandex_mail, "ingest_message", fake_ingest)

    result = yandex_mail.sync_yandex_mail(
        project.id,
        yandex_mail.YandexMailSyncRequest(days=7, max_results=25),
        db_session,
        user,
    )

    assert result["processed"] == 1
    assert result["failed"] == 0
    assert len(ingested) == 1
    assert ingested[0].source_external_id == f"yandex:{project.id}:321:10"
    assert ingested[0].content == "Plain body"
    assert ingested[0].attachments[0]["name"] == "offer.bin"
    assert ("select", "INBOX", True) in client.calls
    assert any(call[0:3] == ("uid", "fetch", "10") and "BODY.PEEK[]" in call[3] for call in client.calls)


def test_sync_request_limits_are_fail_closed():
    for value in (0, 101):
        try:
            yandex_mail.YandexMailSyncRequest(max_results=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"max_results={value} must be rejected")


def test_catalog_offers_configuration_then_readonly_sync(db_session, user_factory, monkeypatch):
    _user, project = _world(db_session, user_factory)
    monkeypatch.setattr("app.integrations.catalog.TelegramChannelAdapter.health", lambda _self: type("Health", (), {"ready": False, "detail": "off"})())
    disconnected = next(item for item in project_integration_catalog(project.id, db_session) if item.key == "yandex_mail:channel")
    assert disconnected.available is True
    assert disconnected.connected is False
    assert disconnected.action == "configure"

    db_session.add(IntegrationCredential(
        project_id=project.id, provider="yandex_mail", capability="channel",
        access_token="encrypted-value", account_email="mailbox@example.test",
    ))
    db_session.commit()
    connected = next(item for item in project_integration_catalog(project.id, db_session) if item.key == "yandex_mail:channel")
    assert connected.connected is True
    assert connected.action == "sync"
    assert connected.detail == "mailbox@example.test · только чтение"

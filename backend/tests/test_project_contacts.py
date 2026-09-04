from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.project_contacts import normalize_email


ROOT = Path(__file__).resolve().parents[1]


def test_normalize_email_accepts_display_name_and_lowercases():
    assert normalize_email('Client Name <SALES@Example.RU>') == "sales@example.ru"


def test_normalize_email_rejects_invalid_value():
    with pytest.raises(HTTPException) as error:
        normalize_email("not-an-email")
    assert error.value.status_code == 422


def test_gmail_only_marks_confirmed_contact_as_deterministic_route():
    contacts = (ROOT / "app/api/project_contacts.py").read_text(encoding="utf-8")
    gmail = (ROOT / "app/api/gmail.py").read_text(encoding="utf-8")
    assert "ProjectContact.confirmed.is_(True)" in contacts
    assert "routing_evidence = None" in gmail
    assert "discover_contact_from_message" in gmail


def test_contact_draft_requires_existing_review_flow():
    contacts = (ROOT / "app/api/project_contacts.py").read_text(encoding="utf-8")
    gmail = (ROOT / "app/api/gmail.py").read_text(encoding="utf-8")
    assert 'status="draft"' in contacts
    assert 'requires_approval": True' in contacts
    assert 'draft.status != "approved"' in gmail


def test_project_contact_migration_contains_discovery_safety_fields():
    migration = (ROOT / "migrations/versions/e6b24a91c301_add_project_contacts.py").read_text(encoding="utf-8")
    assert '"confirmed"' in migration
    assert '"company_activity"' in migration
    assert "uq_project_contact_org_email" in migration

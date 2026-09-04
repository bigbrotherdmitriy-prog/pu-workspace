from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_contacts_are_extracted_from_the_frontend_monolith():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend" / "src" / "modules" / "contacts" / "ContactsModule.tsx").read_text(encoding="utf-8")

    assert 'from "./modules/contacts/ContactsModule"' in app
    assert "<ContactsModule" in app
    assert "async function createProjectContact" not in app
    assert "async function confirmProjectContact" not in app
    assert "async function prepareContactEmail" not in app
    assert 'api("/project-contacts"' in module
    assert "Подтвердить проект" in module
    assert "Отправка возможна только после отдельного подтверждения" in module


def test_contacts_module_keeps_provider_neutral_core_boundary():
    module = (ROOT / "frontend" / "src" / "modules" / "contacts" / "ContactsModule.tsx").read_text(encoding="utf-8")

    assert "projectId" in module
    assert "contractId" in module
    assert "google_workspace" not in module.casefold()
    assert "original" not in module.casefold()
    assert "busy" in module

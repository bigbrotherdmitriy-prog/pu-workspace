from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_project_launch_is_a_separate_frontend_module():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    wizard = (ROOT / "frontend" / "src" / "modules" / "project-launch" / "ProjectLaunchWizard.tsx").read_text(encoding="utf-8")

    assert 'from "./modules/project-launch/ProjectLaunchWizard"' in app
    assert '[Route, "Запуск проекта"]' in app
    assert 'active === "Запуск проекта"' in app
    assert 'active === "Рабочий центр" ? (' in app
    assert "Проект и рабочая папка" in wizard
    assert "ГПР, бюджет и ДДС" in wizard
    assert "Неподтверждённые контакты не маршрутизируют почту автоматически" in wizard
    assert "original" not in wizard.casefold()


def test_project_launch_uses_loaded_core_state_without_provider_calls():
    wizard = (ROOT / "frontend" / "src" / "modules" / "project-launch" / "ProjectLaunchWizard.tsx").read_text(encoding="utf-8")
    hook = (ROOT / "frontend" / "src" / "modules" / "project-launch" / "useProjectLaunchReadiness.ts").read_text(encoding="utf-8")

    assert "state.documents" in wizard
    assert "state.linkedContracts" in wizard
    assert "state.scheduleRows" in wizard
    assert "state.cashFlowRows" in wizard
    assert "state.confirmedContacts" in wizard
    assert "google" not in wizard.casefold()
    assert "launch-readiness" in hook
    assert "sourceReady" in hook
    assert "google" not in hook.casefold()

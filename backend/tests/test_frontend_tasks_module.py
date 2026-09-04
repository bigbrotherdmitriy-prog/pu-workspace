from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "frontend/src/App.tsx"
MODULE = ROOT / "frontend/src/modules/tasks/TasksModule.tsx"


def test_tasks_registry_is_extracted_from_app_shell():
    app = APP.read_text(encoding="utf-8")
    module = MODULE.read_text(encoding="utf-8")

    assert 'from "./modules/tasks/TasksModule"' in app
    assert "<TasksModule" in app
    assert 'className="task-register"' not in app
    assert 'className="card task-register"' in module


def test_task_external_actions_and_completion_require_user_actions():
    module = MODULE.read_text(encoding="utf-8")

    assert "onApproveExternal(task)" in module
    assert "Поставить задачу" in module
    assert "Что выполнено и какой результат получен *" in module
    assert "disabled={!props.completionNote.trim()}" in module
    assert 'onUpdate(task, "completed")' in module
    assert "completionDocumentId" in module
    assert "История задачи и решений" in module

from pathlib import Path


APP_SOURCE = Path(__file__).parents[2] / "frontend" / "src" / "App.tsx"
PROJECT_SELECTION_SOURCE = APP_SOURCE.parent / "context" / "useProjectSelection.ts"
FINANCE_SOURCE = APP_SOURCE.parent / "modules" / "finance" / "useFinanceController.ts"
CONTEXTUAL_AI_SOURCE = APP_SOURCE.parent / "modules" / "ai-secretary" / "ContextualAssistant.tsx"
CONTRACT_PICKER_SOURCE = APP_SOURCE.parent / "modules" / "contracts" / "ContractDocumentPicker.tsx"


def test_new_project_reload_uses_created_project_id():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "async function load(preferredProjectId?: number)" in source
    assert "await activateProject(created.id);" in source
    project_source = PROJECT_SELECTION_SOURCE.read_text(encoding="utf-8")
    assert 'sessionStorage.setItem("pu_active_project_id", String(id));' in project_source


def test_oauth_callback_restores_project_before_initial_load():
    source = PROJECT_SELECTION_SOURCE.read_text(encoding="utf-8")

    assert 'new URLSearchParams(window.location.search).get("project_id")' in source
    assert "const [projectId, setProjectId] = useState(initialProjectId)" in source


def test_project_switch_is_persisted_and_stale_loads_cannot_restore_old_project():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "async function activateProject(id: number)" in source
    project_source = PROJECT_SELECTION_SOURCE.read_text(encoding="utf-8")
    assert "projectIdRef.current = id;" in project_source
    assert "const loadSequence = ++loadSequenceRef.current;" in source
    assert "loadSequence !== loadSequenceRef.current" in source
    assert "onClick={() => activateProject(item.id)}" in source


def test_header_project_switch_updates_persistent_context_before_loading():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "const id = Number(e.target.value);" in source
    assert "rememberProject(id);\n                void load(id);" in source
    assert 'onChange={(e) => setProjectId(Number(e.target.value))}' not in source


def test_authorized_project_without_snapshot_can_select_and_bind_drive_folder():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "!latestSnapshot && googleState?.authorized" in source
    assert "Выбрать рабочую папку" in source
    assert "const targetProjectId = projectIdRef.current;" in source
    assert "await load(targetProjectId);" in source
    assert "Создаётся безопасная копия, выполняются анализ и стандартизация имён" in source
    assert "Подключить и стандартизировать" in source


def test_legacy_ui_keeps_selected_project_after_refresh_and_oauth():
    source = (APP_SOURCE.parents[2] / "backend" / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "function rememberedProjectId()" in source
    assert "async function projects(preferredId=0)" in source
    assert "await projects(created.id)" in source
    assert "projects(rememberedProjectId())" in source


def test_unconfirmed_mail_context_can_be_assigned_to_a_project():
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert "project_id: message.project_id || projectId" in source
    assert "{projects.map((project) => (" in source


def test_unconfirmed_mail_can_be_bulk_moved_to_one_project():
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert '"/ai-secretary/inbox/confirm-context-bulk"' in source
    assert "Выбрать все нераспределённые" in source
    assert "Перенести выбранные" in source


def test_contract_card_exposes_document_schedule_and_cash_flow_chain():
    source = APP_SOURCE.read_text(encoding="utf-8") + CONTRACT_PICKER_SOURCE.read_text(encoding="utf-8")
    assert "linkContractDocument" in source
    assert "Выбрать файл из каталога" in source
    assert "contractCatalogOpen" in source
    assert "Открыть ГПР, бюджет и ДДС" in source
    assert "Договор → ГПР → бюджет → ДДС → акты" in source


def test_finance_workflow_guides_contract_schedule_budget_cash_and_acts():
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert "Мастер запуска исполнения" in source
    assert '<option value="schedule">Этап ГПР</option>' in source
    assert "finance-chain-steps" in source
    assert "Связать с этапом ГПР" in source


def test_finance_workflow_suggests_analyzed_project_documents_without_mutating_sources():
    source = APP_SOURCE.read_text(encoding="utf-8")
    controller = FINANCE_SOURCE.read_text(encoding="utf-8")
    assert "/execution/document-candidates" in controller
    assert "Найденные ГПР, бюджеты, ДДС, счета и акты" in source
    assert "Проверить и использовать" in source
    assert "source_document_id: financeSourceDocumentId || null" in controller
    assert "document_id: financeSourceDocumentId || null" in controller
    assert "/structured-preview" in controller
    assert "/structured-import" in controller
    assert "Создать пакет предложений" in source


def test_ai_secretary_automation_exposes_prepared_task_and_draft_for_review():
    source = APP_SOURCE.read_text(encoding="utf-8")
    assert "Последняя подготовка:" in source
    assert "Открыть задачу" in source
    assert "Открыть черновик" in source
    assert "Внешняя отправка всегда требует вашего подтверждения" in source
    assert "Связать со строкой бюджета" in source


def test_contextual_ai_help_is_available_on_hover_and_keyboard_focus_without_external_call():
    source = APP_SOURCE.read_text(encoding="utf-8")
    helper = CONTEXTUAL_AI_SOURCE.read_text(encoding="utf-8")
    assert "<ContextualAssistant section={active}" in source
    assert 'document.addEventListener("mouseover", show)' in helper
    assert 'document.addEventListener("focusin", show)' in helper
    assert 'document.addEventListener("mouseout", hide)' in helper
    assert 'className="ai-hover-bubble"' in helper
    assert 'className="ai-mascot"' in helper
    assert "api(" not in helper

from pathlib import Path


def test_dashboard_metrics_open_corresponding_registers():
    root = Path(__file__).parents[2] / "frontend" / "src"
    source = (root / "App.tsx").read_text(encoding="utf-8")
    dashboard = (root / "modules" / "dashboard" / "WorkCenterDashboard.tsx").read_text(encoding="utf-8")

    assert 'setTaskFilter("overdue")' in source
    assert 'setObligationFilter("overdue")' in source
    assert 'setGovernanceFocus("risks")' in source
    assert 'setGovernanceFocus("decisions")' in source
    assert 'setNotificationFilter("unread")' in source
    assert 'setActive("Обязательства")' in source
    assert 'setActive("Риски и решения")' in source
    assert 'setActive("Уведомления")' in source
    assert "onOpenOverdueTasks" in dashboard
    assert "onOpenOverdueObligations" in dashboard
    assert "onOpenRisks" in dashboard
    assert "onOpenDecisions" in dashboard
    assert "onOpenNotifications" in dashboard
    assert '<button onClick={() => setActive("Риски и решения")}>Открыть реестр</button>' in source

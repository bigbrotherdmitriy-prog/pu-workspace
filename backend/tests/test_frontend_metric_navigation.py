from pathlib import Path


def test_dashboard_metrics_open_corresponding_registers():
    source = (Path(__file__).parents[2] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

    assert 'function openMetric(label: string)' in source
    assert 'setTaskFilter("overdue")' in source
    assert 'setActive("Обязательства")' in source
    assert 'setActive("Риски и решения")' in source
    assert 'setActive("Уведомления")' in source
    assert 'onClick={() => openMetric(String(label))}' in source
    assert '<button onClick={() => setActive("Риски и решения")}>Открыть реестр</button>' in source

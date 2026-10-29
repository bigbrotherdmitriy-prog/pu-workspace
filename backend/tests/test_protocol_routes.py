"""Маршруты протоколов подключены к приложению."""


def test_protocol_routes_are_registered():
    from app.main import app

    paths = set(app.openapi()["paths"])
    for name in ("insulation", "earthing", "phase-zero", "avr"):
        assert f"/projects/{{project_id}}/executive-docs/{name}-protocol.xlsx" in paths, name

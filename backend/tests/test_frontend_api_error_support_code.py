from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_api_client_exposes_support_code_for_every_failure():
    source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    assert '"X-Request-ID": requestId' in source
    assert 'response.headers.get("X-Request-ID")' in source
    assert "Код обращения:" in source
    assert "class ApiError" in source
    assert "public status: number | null" in source

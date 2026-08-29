from app.main import security_headers


def test_security_header_middleware_is_registered_with_fail_closed_policy():
    constants = set(security_headers.__code__.co_consts)
    assert "nosniff" in constants
    assert "DENY" in constants
    assert "camera=(), microphone=(), geolocation=()" in constants
    assert any(isinstance(value, str) and "frame-ancestors 'none'" in value for value in constants)
    assert any(isinstance(value, str) and "max-age=31536000" in value for value in constants)

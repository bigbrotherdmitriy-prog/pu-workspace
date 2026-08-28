from app.api.analytics import _distribution, router


def test_analytics_route_is_registered():
    assert "/analytics/project" in {route.path for route in router.routes}


def test_distribution_is_sorted_and_handles_missing_values():
    assert _distribution(["email", None, "email", "telegram"]) == [
        {"key": "email", "count": 2},
        {"key": "telegram", "count": 1},
        {"key": "unknown", "count": 1},
    ]

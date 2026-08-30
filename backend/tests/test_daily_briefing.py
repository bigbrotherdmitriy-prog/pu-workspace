from app.api.ai_secretary import router


def test_daily_briefing_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/ai-secretary/daily-briefing" in paths

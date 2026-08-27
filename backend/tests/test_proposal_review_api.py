from app.organizer import router


def test_safe_bulk_approval_route_is_registered():
    paths = {route.path for route in router.routes}
    assert "/organizer/proposals/{proposal_id}/approve-safe" in paths

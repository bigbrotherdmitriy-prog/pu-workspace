"""Pure synthetic envelope tests; no DB, auth override, HTTP or provider."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.api import mobile_sync
from app.api.work_log import WorkLogBody
from app.mobile_work_log_contract import OfflineWorkLogCreateRequest


def payload():
    return {'client_mutation_id':'synthetic-command-1','device_id':'synthetic-device-1',
            'project_id':7,'operation':'work_log.create',
            'client_created_at':'2026-10-30T08:00:00+03:00',
            'data':{'work_date':'2026-10-30','work_name':'Synthetic work','qty':'1.250'}}


def test_explicit_payload_stays_identical_on_serialized_retry():
    original=payload()
    original['data']['labor']=[{'person_name':'Synthetic worker','hours':'2','hourly_rate':'100'}]
    model=OfflineWorkLogCreateRequest.model_validate(original)
    encoded=model.model_dump_json()
    assert OfflineWorkLogCreateRequest.model_validate_json(encoded).model_dump_json()==encoded
    assert model.data.work_date=='2026-10-30'
    assert model.client_created_at.utcoffset().total_seconds()==10800
    assert model.data.labor[0].person_name=='Synthetic worker'
    assert original['data']['qty']=='1.250'


@pytest.mark.parametrize('value',[None,'','20261030','2026-W44-5','2026-02-30','2026-1-03','2026-10-30T00:00:00'])
def test_invalid_date_rejected(value):
    body=payload();body['data']['work_date']=value
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


def test_omitted_date_rejected_without_changing_online_dto():
    body=payload();del body['data']['work_date']
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)
    assert WorkLogBody(work_name='Existing online behavior').work_date is None


@pytest.mark.parametrize('value',[0,-1,True,False,'7',7.0,None])
def test_project_id_is_strict_positive_integer(value):
    body=payload();body['project_id']=value
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


@pytest.mark.parametrize('value',[9007199254740991])
def test_project_id_accepts_max_safe_integer_boundary(value):
    body=payload();body['project_id']=value
    model=OfflineWorkLogCreateRequest.model_validate(body)
    assert model.project_id==value


@pytest.mark.parametrize('value',[9007199254740992,10**100])
def test_project_id_rejects_above_safe_integer_before_service(value):
    body=payload();body['project_id']=value
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


@pytest.mark.parametrize('field',['user_id','author_user_id','organization_id','entity_id'])
@pytest.mark.parametrize('location',['envelope','data'])
def test_identity_and_server_id_not_accepted(field,location):
    body=payload();target=body if location=='envelope' else body['data'];target[field]=99
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


@pytest.mark.parametrize('field,value',[('client_created_at','2026-10-30T08:00:00'),
    ('client_mutation_id','short'),('device_id','spaces not valid'),
    ('operation','task.update')])
def test_invalid_command_metadata_rejected(field,value):
    body=payload();body[field]=value
    with pytest.raises(ValidationError):OfflineWorkLogCreateRequest.model_validate(body)


# ---------------------------------------------------------------------------
# Synthetic direct-handler unit tests for submit_offline_work_log_create.
# No TestClient/HTTP/browser/network/DB. The service is intentionally mocked
# ONLY to verify the adapter wrapper / unit-of-work behavior; actual service
# integration is covered separately.
# ---------------------------------------------------------------------------


class _FakeDb:
    """Bounded fake recording commit/rollback; never touches a real DB."""

    def __init__(self, commit_error=None):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.commit_error = commit_error

    def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self):
        self.rollback_calls += 1


def _actor():
    return SimpleNamespace(id=42)


def _request():
    return OfflineWorkLogCreateRequest.model_validate(payload())


def _install_service(monkeypatch, behavior):
    calls = []

    def fake_service(db, user, request):
        calls.append((db, user, request))
        return behavior(db, user, request)

    monkeypatch.setattr(mobile_sync, 'submit_work_log_create', fake_service)
    return calls


def test_direct_handler_success_returns_same_ack_and_commits_once(monkeypatch):
    ack = object()
    calls = _install_service(monkeypatch, lambda db, user, request: ack)
    db = _FakeDb()
    actor = _actor()
    request = _request()

    result = mobile_sync.submit_offline_work_log_create(
        request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert result is ack
    assert len(calls) == 1
    assert calls[0][0] is db
    assert calls[0][1] is actor
    assert calls[0][2] is request
    assert db.commit_calls == 1
    assert db.rollback_calls == 0


def test_direct_handler_header_mismatch_403_before_service(monkeypatch):
    calls = _install_service(monkeypatch, lambda db, user, request: object())
    db = _FakeDb()
    actor = _actor()
    request = _request()

    with pytest.raises(HTTPException) as excinfo:
        mobile_sync.submit_offline_work_log_create(
            request, x_pu_expected_user=actor.id + 1, db=db, user=actor)

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == {'code': 'expected_user_mismatch'}
    assert calls == []
    assert db.commit_calls == 0
    assert db.rollback_calls == 0


@pytest.mark.parametrize('status', [401, 403])
def test_direct_handler_service_http_exception_unchanged(monkeypatch, status):
    original = HTTPException(status, {'code': 'synthetic_service_error'})

    def behavior(db, user, request):
        raise original

    _install_service(monkeypatch, behavior)
    db = _FakeDb()
    actor = _actor()
    request = _request()

    with pytest.raises(HTTPException) as excinfo:
        mobile_sync.submit_offline_work_log_create(
            request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert excinfo.value is original
    assert excinfo.value.status_code == status
    assert excinfo.value.detail == {'code': 'synthetic_service_error'}
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


def test_direct_handler_lookup_error_maps_to_404(monkeypatch):
    def behavior(db, user, request):
        raise LookupError('synthetic missing project')

    _install_service(monkeypatch, behavior)
    db = _FakeDb()
    actor = _actor()
    request = _request()

    with pytest.raises(HTTPException) as excinfo:
        mobile_sync.submit_offline_work_log_create(
            request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == {
        'code': 'project_not_found', 'message': 'synthetic missing project'}
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


@pytest.mark.parametrize('code', ['client_mutation_id_reused', 'offline_receipt_not_applied'])
def test_direct_handler_value_error_conflict_maps_to_409(monkeypatch, code):
    def behavior(db, user, request):
        raise ValueError(code)

    _install_service(monkeypatch, behavior)
    db = _FakeDb()
    actor = _actor()
    request = _request()

    with pytest.raises(HTTPException) as excinfo:
        mobile_sync.submit_offline_work_log_create(
            request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {'code': code}
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


def test_direct_handler_other_value_error_maps_to_422(monkeypatch):
    def behavior(db, user, request):
        raise ValueError('synthetic invalid value')

    _install_service(monkeypatch, behavior)
    db = _FakeDb()
    actor = _actor()
    request = _request()

    with pytest.raises(HTTPException) as excinfo:
        mobile_sync.submit_offline_work_log_create(
            request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == {
        'code': 'invalid_work_log_create', 'message': 'synthetic invalid value'}
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


def test_direct_handler_unexpected_error_rethrown_same_object(monkeypatch):
    original = RuntimeError('synthetic unexpected')

    def behavior(db, user, request):
        raise original

    _install_service(monkeypatch, behavior)
    db = _FakeDb()
    actor = _actor()
    request = _request()

    with pytest.raises(RuntimeError) as excinfo:
        mobile_sync.submit_offline_work_log_create(
            request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert excinfo.value is original
    assert db.rollback_calls == 1
    assert db.commit_calls == 0


def test_direct_handler_commit_failure_rethrown_same_object(monkeypatch):
    ack = object()
    commit_error = RuntimeError('synthetic commit failure')
    _install_service(monkeypatch, lambda db, user, request: ack)
    db = _FakeDb(commit_error=commit_error)
    actor = _actor()
    request = _request()

    with pytest.raises(RuntimeError) as excinfo:
        mobile_sync.submit_offline_work_log_create(
            request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert excinfo.value is commit_error
    assert db.commit_calls == 1
    assert db.rollback_calls == 1


def test_direct_handler_preserves_request_and_actor_inputs(monkeypatch):
    ack = object()
    calls = _install_service(monkeypatch, lambda db, user, request: ack)
    db = _FakeDb()
    actor = _actor()
    request = _request()
    before = request.model_dump_json()

    mobile_sync.submit_offline_work_log_create(
        request, x_pu_expected_user=actor.id, db=db, user=actor)

    assert calls[0][2] is request
    assert calls[0][1] is actor
    assert request.model_dump_json() == before
    assert actor.id == 42


# ---------------------------------------------------------------------------
# Pure FastAPI route/OpenAPI metadata checks. Constructs FastAPI and
# include_router ONLY (no client/server). This does NOT claim actual HTTP
# request validation; it only inspects declared route metadata.
# ---------------------------------------------------------------------------


def _work_log_create_route():
    app = FastAPI()
    app.include_router(mobile_sync.router)
    path = mobile_sync.router.prefix + '/work-log-create'
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and 'POST' in route.methods:
            return route
    raise AssertionError('work-log-create route not found')


def test_route_metadata_post_path_and_header_contract():
    route = _work_log_create_route()
    assert route.path == '/mobile-sync/work-log-create'
    assert 'POST' in route.methods

    header_params = [p for p in route.dependant.header_params
                     if p.alias == 'X-PU-Expected-User']
    assert len(header_params) == 1
    header = header_params[0]
    assert header.required is True

    schema = header.field_info
    assert schema.gt == 0
    assert schema.le == 9007199254740991


def test_route_metadata_request_body_references_dto():
    route = _work_log_create_route()
    body_params = [p for p in route.dependant.body_params
                   if p.name == 'request']
    assert len(body_params) == 1
    assert body_params[0].field_info.annotation is OfflineWorkLogCreateRequest

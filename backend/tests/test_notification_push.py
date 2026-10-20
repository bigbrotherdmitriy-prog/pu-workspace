"""V6-04 FCM push outbox. FCM is mocked; no test touches the network."""
import json
from datetime import datetime, time, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app import notification_push
from app.core import readiness as readiness_module
from app.integrations import push
from app.integrations.push import FcmPushAdapter, PushTransientError, push_message_for
from app.jobs.handlers import run
from app.jobs.queue import claim, fail, succeed
from app.models.job import BackgroundJob
from app.models.management import Notification, NotificationPolicy
from app.models.notification_device import UserDeviceToken
from test_mvp3_hardening import world

# In the past, so the real-clock queue claim always sees queued jobs as due.
NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)  # 12:00 in Moscow
TOKEN = "fcm-synthetic-token:APA91b" + "y" * 40
SECRET_TITLE = "Оплата 1 250 000,00 руб. по счёту №4711 ООО «Ромашка»"
SECRET_BODY = "Цена за м³: 98 765,43 ₽. Фрагмент договора: «штраф 0,1% в день»"


class FakeFcm:
    """Records request bodies and answers with the next scripted response."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, {"name": "projects/p/messages/1"})]
        self.bodies: list[dict] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer synthetic-access-token"
        self.bodies.append(json.loads(request.content))
        status, body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    def adapter(self) -> FcmPushAdapter:
        return FcmPushAdapter(
            access_token=lambda info: "synthetic-access-token",
            http_client=lambda: httpx.Client(transport=httpx.MockTransport(self._handle)),
        )


def _fcm_error(status: int, code: str) -> tuple[int, dict]:
    return status, {"error": {"code": status, "status": code, "details": [
        {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}]}}


@pytest.fixture
def service_account(tmp_path, monkeypatch):
    path = tmp_path / "fcm.json"
    path.write_text(json.dumps({
        "type": "service_account", "project_id": "synthetic-project",
        "client_email": "push@synthetic-project.iam.gserviceaccount.com",
        "private_key": "synthetic-not-a-key",
        "token_uri": "https://oauth2.googleapis.com/token",
    }), encoding="utf-8")
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON_PATH", str(path))
    return path


@pytest.fixture
def fcm(db_session, service_account):
    fake = FakeFcm()
    clock = {"now": NOW}
    notification_push.install_push_runtime(lambda: db_session, adapter_factory=fake.adapter,
                                           clock=lambda: clock["now"])
    yield fake, clock
    notification_push.install_push_runtime()


@pytest.fixture
def scene(db_session, user_factory):
    _, user, project, _ = world(db_session, user_factory)
    device = UserDeviceToken(user_id=user.id, fcm_token=TOKEN, platform="android")
    db_session.add(device); db_session.commit()
    return user, project, device


def _notify(db, user, project, *, kind="deadline", title=SECRET_TITLE, body=SECRET_BODY, key="n-1",
            entity_type="obligation", entity_id=None):
    # A distinct entity per key unless a test pins it (the 60 s per-entity limit).
    entity_id = entity_id if entity_id is not None else 1000 + sum(map(ord, key))
    notification = Notification(project_id=project.id, user_id=user.id, kind=kind, title=title,
                                body=body, entity_type=entity_type, entity_id=entity_id, dedupe_key=key)
    db.add(notification); db.commit()
    return notification


def _push_jobs(db):
    return list(db.scalars(select(BackgroundJob).where(BackgroundJob.kind == "notifications.push.deliver")
                           .order_by(BackgroundJob.id)))


def _work_once(db, worker="worker-1"):
    """One iteration of app.jobs.worker without threads."""
    job = claim(db, worker)
    assert job is not None
    try:
        result = run(job.kind, dict(job.payload))
    except Exception as exc:
        return fail(db, job.id, worker, exc, retryable=not isinstance(exc, (KeyError, TypeError, ValueError)))
    succeed(db, job.id, worker, result)
    return result


def _make_due(db):
    for job in _push_jobs(db):
        job.available_at = NOW - timedelta(seconds=1)
    db.commit()


def test_new_notification_queues_one_job_per_device_in_the_same_transaction(db_session, fcm, scene):
    user, project, device = scene
    db_session.add(UserDeviceToken(user_id=user.id, fcm_token=TOKEN + "-2", platform="android"))
    db_session.commit()
    notification = _notify(db_session, user, project)
    jobs = _push_jobs(db_session)
    assert [job.payload for job in jobs] == [
        {"notification_id": notification.id, "device_id": device.id},
        {"notification_id": notification.id, "device_id": device.id + 1},
    ]
    assert all(job.status == "queued" and job.max_attempts == notification_push.PUSH_MAX_ATTEMPTS for job in jobs)
    # The payload stores identifiers only, never the token or the text.
    assert TOKEN not in json.dumps([job.payload for job in jobs])


def test_rolled_back_notification_leaves_no_push_job(db_session, fcm, scene):
    user, project, _ = scene
    db_session.add(Notification(project_id=project.id, user_id=user.id, kind="risk", title="t", body="b",
                                entity_type="risk", entity_id=1, dedupe_key="rolled-back"))
    db_session.flush()
    db_session.rollback()
    assert _push_jobs(db_session) == []


def test_push_payload_has_title_and_deep_link_only(db_session, fcm, scene):
    fake, _ = fcm
    user, project, _ = scene
    _notify(db_session, user, project)
    assert _work_once(db_session) == {"status": "sent"}
    [body] = fake.bodies
    message = body["message"]
    assert set(message) == {"token", "notification", "data", "android"}
    assert message["notification"] == {"title": "Приближается срок обязательства"}
    # No body is forwarded for non-invoice kinds, even when the record has one.
    assert "body" not in message["notification"]
    assert message["data"] == {"deep_link": f"https://puworkspace.ru/new/?project_id={project.id}&section=notifications"}
    assert message["android"] == {"priority": "high", "notification": {"channel_id": "pu_workspace_alerts"}}
    sent = json.dumps(body, ensure_ascii=False)
    for leaked in ("1 250 000", "4711", "Ромашка", "98 765", "₽", "руб", "штраф", "Срок:", SECRET_TITLE[:10]):
        assert leaked not in sent


@pytest.mark.parametrize("kind", [*push.KIND_TITLES, "unknown_kind"])
def test_every_push_title_is_fixed_text_without_digits(kind):
    message = push_message_for(kind=kind, project_id=17)
    assert message.title in {*push.KIND_TITLES.values(), push.DEFAULT_TITLE}
    assert not any(char.isdigit() for char in message.title)
    assert message.link.startswith("https://puworkspace.ru/new/?")


def test_fcm_outage_is_retried_later_instead_of_losing_the_push(db_session, fcm, scene):
    fake, _ = fcm
    fake.responses = [(503, {"error": {"status": "UNAVAILABLE"}}),
                      (0, httpx.ConnectError("synthetic outage")),
                      (200, {"name": "projects/p/messages/2"})]
    user, project, _ = scene
    _notify(db_session, user, project)
    assert _work_once(db_session) == "retrying"
    [job] = _push_jobs(db_session)
    assert job.last_error == "PushTransientError" and job.available_at.replace(tzinfo=timezone.utc) > NOW
    _make_due(db_session)
    assert _work_once(db_session) == "retrying"
    _make_due(db_session)
    assert _work_once(db_session) == {"status": "sent"}
    db_session.refresh(job)
    assert job.status == "completed" and job.attempts == 3 and len(fake.bodies) == 3


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_auth_quota_and_server_errors_are_retryable(db_session, fcm, scene, status):
    fake, _ = fcm
    fake.responses = [(status, {"error": {"status": "WHATEVER"}})]
    user, project, _ = scene
    _notify(db_session, user, project)
    assert _work_once(db_session) == "retrying"


def _bad_request(field: str) -> tuple[int, dict]:
    status, body = _fcm_error(400, "INVALID_ARGUMENT")
    body["error"]["details"].append({"@type": "type.googleapis.com/google.rpc.BadRequest",
                                     "fieldViolations": [{"field": field, "description": "synthetic"}]})
    return status, body


@pytest.mark.parametrize("response,reason", [
    (_fcm_error(404, "UNREGISTERED"), "UNREGISTERED"),
    (_bad_request("message.token"), "INVALID_ARGUMENT"),
])
def test_dead_token_is_deleted(db_session, fcm, scene, response, reason):
    fake, _ = fcm
    fake.responses = [response]
    user, project, _ = scene
    _notify(db_session, user, project)
    assert _work_once(db_session) == {"status": "token_removed", "reason": reason}
    assert db_session.scalar(select(UserDeviceToken)) is None


@pytest.mark.parametrize("response", [
    _fcm_error(400, "INVALID_ARGUMENT"),
    _bad_request("message.android.notification.channel_id"),
    (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}}),
])
def test_malformed_request_fails_without_retry_and_keeps_the_token(db_session, fcm, scene, response, caplog):
    fake, _ = fcm
    fake.responses = [response]
    user, project, _ = scene
    _notify(db_session, user, project)
    with caplog.at_level("ERROR", logger="pu.push"):
        assert _work_once(db_session) == "failed"
    [job] = _push_jobs(db_session)
    assert job.last_error == "PushRequestRejected"
    assert db_session.scalar(select(UserDeviceToken)).fcm_token == TOKEN
    [record] = [r for r in caplog.records if r.name == "pu.push"]
    assert "http=400" in record.getMessage() and "status=INVALID_ARGUMENT" in record.getMessage()
    assert TOKEN not in record.getMessage() and "deep_link" not in record.getMessage()


def test_no_device_or_disabled_channel_queues_nothing(db_session, fcm, user_factory, monkeypatch):
    _, user, project, _ = world(db_session, user_factory)
    _notify(db_session, user, project, key="no-device")
    assert _push_jobs(db_session) == []
    db_session.add(UserDeviceToken(user_id=user.id, fcm_token=TOKEN, platform="android")); db_session.commit()
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_JSON_PATH")
    _notify(db_session, user, project, key="channel-off")
    assert _push_jobs(db_session) == []


def test_quiet_hours_delay_the_push_to_local_morning(db_session, fcm, scene):
    _, clock = fcm
    user, project, _ = scene
    db_session.add(NotificationPolicy(organization_id=project.organization_id, project_id=project.id,
                                      user_id=user.id, timezone="Europe/Moscow",
                                      quiet_start=time(22), quiet_end=time(7)))
    db_session.commit()
    clock["now"] = datetime(2026, 9, 1, 20, 30, tzinfo=timezone.utc)  # 23:30 Moscow
    _notify(db_session, user, project)
    [job] = _push_jobs(db_session)
    assert job.available_at.replace(tzinfo=timezone.utc) == datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc)


def test_retry_that_lands_in_quiet_hours_is_deferred_not_sent(db_session, fcm, scene):
    fake, clock = fcm
    user, project, _ = scene
    _notify(db_session, user, project)
    db_session.add(NotificationPolicy(organization_id=project.organization_id, project_id=project.id,
                                      user_id=user.id, timezone="Europe/Moscow",
                                      quiet_start=time(22), quiet_end=time(7)))
    db_session.commit()
    clock["now"] = datetime(2026, 9, 1, 20, 30, tzinfo=timezone.utc)
    result = _work_once(db_session)
    assert result["status"] == "deferred" and fake.bodies == []
    deferred = _push_jobs(db_session)[-1]
    assert deferred.status == "queued"
    assert deferred.available_at.replace(tzinfo=timezone.utc) == datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc)


def test_disabled_policy_suppresses_push(db_session, fcm, scene):
    user, project, _ = scene
    db_session.add(NotificationPolicy(organization_id=project.organization_id, project_id=project.id,
                                      user_id=user.id, enabled=False))
    db_session.commit()
    _notify(db_session, user, project)
    assert _push_jobs(db_session) == []


def test_device_rebound_to_another_user_or_read_notification_is_skipped(db_session, fcm, scene, user_factory):
    fake, _ = fcm
    user, project, device = scene
    _notify(db_session, user, project, key="first")
    other = user_factory()
    device.user_id = other.id; db_session.commit()
    assert _work_once(db_session) == {"status": "skipped", "reason": "device_gone"}
    device.user_id = user.id; db_session.commit()
    second = _notify(db_session, user, project, key="second")
    second.is_read = True; db_session.commit()
    assert _work_once(db_session) == {"status": "skipped", "reason": "already_read"}
    assert fake.bodies == []


def test_malformed_job_payload_is_not_retried(db_session, fcm):
    with pytest.raises(ValueError):
        notification_push.run_push_delivery({"notification_id": "1", "device_id": 2})


def test_channel_health_and_readiness_explain_why_push_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_JSON_PATH", raising=False)
    assert push.fcm_health().detail == "FCM_SERVICE_ACCOUNT_JSON_PATH is not set"
    report = readiness_module.readiness_report(include_live_services=False)
    assert report["push_ready"] is False
    assert report["checks"]["fcm_push"] == {
        "ok": False, "required": False, "message": "push disabled: FCM_SERVICE_ACCOUNT_JSON_PATH is not set"}
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON_PATH", str(tmp_path / "missing.json"))
    assert push.fcm_health().detail == "service account file not found"
    # A configured but broken secret is a startup failure, not a silent "off".
    broken_report = readiness_module.readiness_report(include_live_services=False)
    assert broken_report["checks"]["fcm_push"]["required"] is True
    assert broken_report["ready"] is False
    broken = tmp_path / "broken.json"
    broken.write_text('{"type": "authorized_user"}', encoding="utf-8")
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON_PATH", str(broken))
    assert push.fcm_health().detail == "service account file is invalid"


def test_channel_is_ready_with_a_service_account(service_account):
    assert push.fcm_health().ready is True
    assert readiness_module.readiness_report(include_live_services=False)["checks"]["fcm_push"]["ok"] is True


def test_adapter_sends_to_the_service_account_project(service_account):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"name": "projects/synthetic-project/messages/1"})

    adapter = FcmPushAdapter(access_token=lambda info: "synthetic-access-token",
                             http_client=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert adapter.send(TOKEN, push_message_for(kind="risk", project_id=3)) == "projects/synthetic-project/messages/1"
    assert seen == ["https://fcm.googleapis.com/v1/projects/synthetic-project/messages:send"]
    with pytest.raises(PushTransientError):
        FcmPushAdapter(access_token=lambda info: "t",
                       http_client=lambda: httpx.Client(transport=httpx.MockTransport(
                           lambda request: httpx.Response(502, text="bad gateway")))).send(
            TOKEN, push_message_for(kind="risk", project_id=3))


def test_task_push_links_to_the_task_card():
    message = push_message_for(kind="task_assigned", project_id=17, entity_type="task", entity_id=42)
    assert message.title == "Вам назначена задача"
    assert message.link == "https://puworkspace.ru/new/?project_id=17&task=42"


def test_invoice_from_mail_push_carries_the_server_built_body(db_session, fcm, scene):
    fake, _ = fcm
    user, project, _ = scene
    _notify(db_session, user, project, kind="invoice_from_mail",
            title="Счёт из почты",
            body="ООО «Ромашка» — 1 250 ₽. Нажмите, чтобы подтвердить.",
            entity_type="invoice_extraction_proposal", entity_id=99)
    assert _work_once(db_session) == {"status": "sent"}
    message = fake.bodies[0]["message"]
    assert message["notification"] == {
        "title": "Счёт из почты — подтвердить?",
        "body": "ООО «Ромашка» — 1 250 ₽. Нажмите, чтобы подтвердить.",
    }
    assert message["data"] == {"deep_link": f"https://puworkspace.ru/new/?project_id={project.id}&invoice=99"}


def test_long_body_is_truncated_to_180_chars(db_session, fcm, scene):
    fake, _ = fcm
    user, project, _ = scene
    long_body = "А" * 500
    _notify(db_session, user, project, kind="invoice_from_mail", title="Счёт из почты", body=long_body,
            entity_type="invoice_extraction_proposal", entity_id=99)
    assert _work_once(db_session) == {"status": "sent"}
    assert fake.bodies[0]["message"]["notification"]["body"] == "А" * 180


def test_invoice_proposal_push_links_to_the_confirmation_card():
    message = push_message_for(kind="invoice_from_mail", project_id=17,
                               entity_type="invoice_extraction_proposal", entity_id=99,
                               body="ООО «Ромашка» — 1 250 ₽. Нажмите, чтобы подтвердить.")
    assert message.title == "Счёт из почты — подтвердить?"
    assert message.link == "https://puworkspace.ru/new/?project_id=17&invoice=99"
    assert "Нажмите, чтобы подтвердить." in message.body


def test_body_is_only_forwarded_for_invoice_from_mail():
    with_body = push_message_for(kind="invoice_from_mail", project_id=17,
                                 entity_type="invoice_extraction_proposal", entity_id=99,
                                 body="short safe text")
    assert with_body.body == "short safe text"
    for other in ("task_assigned", "deadline", "risk", "unknown_kind"):
        message = push_message_for(kind=other, project_id=17, entity_type="obligation", entity_id=1,
                                   body="must not leak")
        assert message.body == ""


def test_fcm_body_includes_notification_body_only_when_set():
    with_body = push_message_for(kind="invoice_from_mail", project_id=17,
                                 entity_type="invoice_extraction_proposal", entity_id=99,
                                 body="Краткий текст")
    assert with_body.fcm_body(TOKEN)["message"]["notification"] == {
        "title": "Счёт из почты — подтвердить?", "body": "Краткий текст"}
    without_body = push_message_for(kind="deadline", project_id=17)
    assert "body" not in without_body.fcm_body(TOKEN)["message"]["notification"]


def test_one_push_per_entity_per_minute(db_session, fcm, scene):
    fake, clock = fcm
    user, project, _ = scene
    _notify(db_session, user, project, key="first", entity_type="risk", entity_id=7)
    clock["now"] = NOW + timedelta(seconds=30)
    _notify(db_session, user, project, key="second", entity_type="risk", entity_id=7)
    _notify(db_session, user, project, key="other-entity", entity_type="risk", entity_id=8)
    assert len(_push_jobs(db_session)) == 2
    clock["now"] = NOW + timedelta(seconds=61)
    _notify(db_session, user, project, key="third", entity_type="risk", entity_id=7)
    assert len(_push_jobs(db_session)) == 3


def test_task_reassignment_notifies_the_new_assignee_and_pushes(db_session, fcm, user_factory):
    from app.models.project_member import ProjectMember
    from app.models.task import Task
    from app.task_mutations import apply_task_patch

    fake, _ = fcm
    _, manager, project, _ = world(db_session, user_factory)
    assignee = user_factory()
    db_session.add(ProjectMember(project_id=project.id, user_id=assignee.id, role="editor"))
    db_session.add(UserDeviceToken(user_id=assignee.id, fcm_token=TOKEN, platform="android"))
    task = Task(project_id=project.id, assignee_user_id=manager.id, created_by_user_id=manager.id,
                title="Оплатить 3 400 000 ₽ подрядчику", status="assigned", priority="normal",
                source_type="manual", source_file_id="s", source_file_name="s.txt", source_excerpt="e",
                source_excerpt_hash="1" * 64, confidence=1.0)
    db_session.add(task); db_session.commit()

    apply_task_patch(db_session, task=task, actor=manager, changes={"assignee_user_id": assignee.id},
                     fields_set={"assignee_user_id"})
    db_session.commit()

    notification = db_session.scalar(select(Notification).where(Notification.kind == "task_assigned"))
    assert notification.user_id == assignee.id and notification.entity_id == task.id
    assert "3 400 000" not in notification.title + notification.body
    assert _work_once(db_session) == {"status": "sent"}
    message = fake.bodies[0]["message"]
    assert message["notification"] == {"title": "Вам назначена задача"}
    assert message["data"] == {"deep_link": f"https://puworkspace.ru/new/?project_id={project.id}&task={task.id}"}


def test_self_assignment_creates_no_notification(db_session, fcm, user_factory):
    from app.models.task import Task
    from app.task_mutations import apply_task_patch

    _, manager, project, _ = world(db_session, user_factory)
    task = Task(project_id=project.id, assignee_user_id=manager.id, created_by_user_id=manager.id,
                title="t", status="assigned", priority="normal", source_type="manual", source_file_id="s",
                source_file_name="s.txt", source_excerpt="e", source_excerpt_hash="1" * 64, confidence=1.0)
    db_session.add(task); db_session.commit()
    apply_task_patch(db_session, task=task, actor=manager, changes={"assignee_user_id": manager.id},
                     fields_set={"assignee_user_id"})
    db_session.commit()
    assert db_session.scalar(select(Notification).where(Notification.kind == "task_assigned")) is None

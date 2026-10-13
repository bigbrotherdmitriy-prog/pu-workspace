"""Firebase Cloud Messaging (HTTP v1) channel for Android push (V6-04).

The only secret is a Google service account JSON whose path comes from
``FCM_SERVICE_ACCOUNT_JSON_PATH``.  Without it the channel is off and
readiness reports why; nothing is sent and nothing is queued.

A push carries a fixed title and a deep link only.  Notification titles and
bodies are never forwarded: they can quote amounts, prices or document text,
and FCM payloads leave our infrastructure.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable

import httpx

from app.integrations.contracts import AdapterHealth

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
APP_BASE_URL = "https://puworkspace.ru/new/"
ANDROID_CHANNEL_ID = "pu_workspace_alerts"
# FCM errorCode values meaning the token itself is dead; the row is deleted.
REJECTED_TOKEN_CODES = frozenset({"UNREGISTERED", "INVALID_ARGUMENT"})
_SERVICE_ACCOUNT_FIELDS = ("type", "project_id", "client_email", "private_key", "token_uri")

# Fixed, content-free titles per Notification.kind.
KIND_TITLES = {
    "task_assigned": "Вам назначена задача",
    "deadline": "Приближается срок обязательства",
    "overdue": "Обязательство просрочено",
    "risk": "Риск требует внимания",
    "decision": "Решение требует внимания",
    "management_digest": "Сводка управления проектом",
    "auto_task_due_soon": "Задача требует внимания",
    "mobile_sync_conflict": "Конфликт синхронизации разрешён",
}
DEFAULT_TITLE = "Новое уведомление в PU Workspace"


class PushError(RuntimeError):
    """Base class; RuntimeError keeps durable jobs retryable."""


class PushNotConfigured(PushError):
    pass


class PushTransientError(PushError):
    """FCM or the network failed; the same message may succeed later."""


class PushTokenRejected(Exception):
    """FCM says the registration token is dead; retrying cannot help."""


@dataclass(frozen=True, slots=True)
class PushMessage:
    title: str
    link: str

    def fcm_body(self, token: str) -> dict:
        return {"message": {
            "token": token,
            "notification": {"title": self.title},
            "data": {"deep_link": self.link},
            "android": {"priority": "high", "notification": {"channel_id": ANDROID_CHANNEL_ID}},
        }}


def push_message_for(*, kind: str, project_id: int, entity_type: str = "", entity_id: int | None = None) -> PushMessage:
    """Build a push from identifiers only; no free text from the record.

    A task opens its card in the task list; anything else opens the project's
    notification list, where the full text is shown after sign-in.
    """
    if entity_type == "task" and entity_id:
        link = f"{APP_BASE_URL}?project_id={int(project_id)}&task={int(entity_id)}"
    else:
        link = f"{APP_BASE_URL}?project_id={int(project_id)}&section=notifications"
    return PushMessage(title=KIND_TITLES.get(kind, DEFAULT_TITLE), link=link)


def fcm_service_account_path() -> str:
    return os.getenv("FCM_SERVICE_ACCOUNT_JSON_PATH", "").strip()


def _load_service_account() -> dict:
    path = fcm_service_account_path()
    if not path:
        raise PushNotConfigured("FCM_SERVICE_ACCOUNT_JSON_PATH is not set")
    try:
        with open(path, encoding="utf-8") as handle:
            info = json.load(handle)
    except FileNotFoundError:
        raise PushNotConfigured("service account file not found") from None
    except (OSError, ValueError):
        raise PushNotConfigured("service account file is unreadable") from None
    if not isinstance(info, dict) or info.get("type") != "service_account" or not all(
        isinstance(info.get(name), str) and info.get(name) for name in _SERVICE_ACCOUNT_FIELDS
    ):
        raise PushNotConfigured("service account file is invalid")
    return info


def fcm_health() -> AdapterHealth:
    try:
        _load_service_account()
    except PushNotConfigured as error:
        return AdapterHealth(ready=False, detail=str(error))
    return AdapterHealth(ready=True, detail="configured")


def fcm_configured() -> bool:
    return fcm_health().ready


def _service_account_token(info: dict) -> str:
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    try:
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[FCM_SCOPE])
        credentials.refresh(Request())
    except GoogleAuthError as error:
        raise PushTransientError("FCM access token unavailable") from error
    except ValueError:
        raise PushNotConfigured("service account file is invalid") from None
    return str(credentials.token)


def _error_code(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return ""
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"])
    return str(error.get("status") or "")


class FcmPushAdapter:
    provider = "fcm"

    def __init__(self, *, access_token: Callable[[dict], str] = _service_account_token,
                 http_client: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=10.0)):
        self._access_token = access_token
        self._http_client = http_client

    def health(self) -> AdapterHealth:
        return fcm_health()

    def send(self, token: str, message: PushMessage) -> str:
        info = _load_service_account()
        bearer = self._access_token(info)
        url = FCM_SEND_URL.format(project_id=info["project_id"])
        try:
            with self._http_client() as client:
                response = client.post(url, json=message.fcm_body(token),
                                       headers={"Authorization": f"Bearer {bearer}"})
        except httpx.HTTPError as error:
            raise PushTransientError(error.__class__.__name__) from None
        if response.status_code == 200:
            # Delivered; an odd answer body must not turn into a failed job.
            try:
                return str((response.json() or {}).get("name", "sent"))
            except (ValueError, AttributeError):
                return "sent"
        code = _error_code(response)
        if code in REJECTED_TOKEN_CODES:
            raise PushTokenRejected(code)
        # 401/403 (key revoked), 429 (quota), 5xx (outage) and any unknown
        # answer: keep the message and let the durable queue retry.
        raise PushTransientError(f"FCM HTTP {response.status_code} {code}".strip())

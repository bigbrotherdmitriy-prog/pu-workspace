"""Isolated sink-only Google Tasks bridge for the V5.4 S10 live gate.

This is intentionally a separate ASGI application.  It does not import the
product database or product OAuth credentials and it cannot accept message
content or recipient addresses.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from pydantic import BaseModel, ConfigDict, Field


TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
OAUTH_SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email", TASKS_SCOPE)
CAPABILITIES_SCHEMA = "puw.v54.live-provider.capabilities.v1"
TASK_LIST_TITLE = "PU Workspace S10 Sandbox"
HEX64 = r"^[0-9a-f]{64}$"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    bridge_token: str
    setup_token: str
    fernet_key: str
    client_id: str
    client_secret: str
    redirect_uri: str
    expected_email: str
    data_dir: Path
    cleanup_ttl_seconds: int = 300

    @classmethod
    def from_env(cls) -> "Settings":
        ttl = int(os.getenv("PUW_S10_CLEANUP_TTL_SECONDS", "300"))
        if not 60 <= ttl <= 3600:
            raise RuntimeError("PUW_S10_CLEANUP_TTL_SECONDS must be between 60 and 3600")
        bridge_token = _required("PUW_S10_BRIDGE_TOKEN")
        setup_token = _required("PUW_S10_SETUP_TOKEN")
        if len(bridge_token) < 24 or len(setup_token) < 24:
            raise RuntimeError("bridge and setup tokens must contain at least 24 characters")
        return cls(
            bridge_token=bridge_token,
            setup_token=setup_token,
            fernet_key=_required("PUW_S10_FERNET_KEY"),
            client_id=_required("PUW_S10_GOOGLE_CLIENT_ID"),
            client_secret=_required("PUW_S10_GOOGLE_CLIENT_SECRET"),
            redirect_uri=_required("PUW_S10_GOOGLE_REDIRECT_URI"),
            expected_email=_required("PUW_S10_EXPECTED_EMAIL").casefold(),
            data_dir=Path(os.getenv("PUW_S10_DATA_DIR", "/data")),
            cleanup_ttl_seconds=ttl,
        )


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: str = Field(pattern=HEX64)
    command_key: str = Field(pattern=HEX64)
    idempotency_key: str = Field(pattern=HEX64)
    payload_hash: str = Field(pattern=HEX64)
    account_fingerprint: str = Field(pattern=HEX64)
    run_nonce: str = Field(pattern=HEX64)


class EffectCommand(Command):
    fault: str = Field(pattern=r"^timeout-after-effect$")


class EncryptedState:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.data_dir / "state.enc"
        self.lock = threading.RLock()
        self.cipher = Fernet(settings.fernet_key.encode("ascii"))
        settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.exists():
                return {}
            try:
                raw = self.cipher.decrypt(self.path.read_bytes())
                value = json.loads(raw.decode("utf-8"))
            except (InvalidToken, OSError, UnicodeError, ValueError) as exc:
                raise RuntimeError("encrypted bridge state is unreadable") from exc
            if not isinstance(value, dict):
                raise RuntimeError("encrypted bridge state is invalid")
            return value

    def save(self, value: dict[str, Any]) -> None:
        with self.lock:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
            temporary = self.path.with_suffix(".tmp")
            temporary.write_bytes(self.cipher.encrypt(encoded))
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)


class GoogleTasksSink:
    def __init__(self, state: EncryptedState):
        self.state = state
        self.lock = threading.RLock()

    def _credentials(self, record: dict[str, Any]) -> Credentials:
        credentials = Credentials.from_authorized_user_info(record["credentials"], OAUTH_SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            record["credentials"] = json.loads(credentials.to_json())
            self.state.save(record)
        return credentials

    def _service(self, record: dict[str, Any]):
        return build("tasks", "v1", credentials=self._credentials(record), cache_discovery=False)

    @staticmethod
    def _marker(command: Command) -> str:
        return "puw-s10:" + ":".join((command.run_nonce, command.command_key, command.payload_hash))

    def configured(self) -> bool:
        record = self.state.load()
        return bool(record.get("credentials") and record.get("tasklist_id") and record.get("account_fingerprint"))

    def account_fingerprint(self) -> str:
        value = self.state.load().get("account_fingerprint", "")
        if not isinstance(value, str) or len(value) != 64:
            raise HTTPException(503, "sandbox identity is not configured")
        return value

    def list_active(self, service: Any, tasklist_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = None
        while True:
            response = service.tasks().list(
                tasklist=tasklist_id,
                showCompleted=True,
                showDeleted=False,
                showHidden=True,
                maxResults=100,
                pageToken=page_token,
            ).execute()
            items.extend(item for item in response.get("items", []) if not item.get("deleted"))
            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def matching(self, command: Command) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
        record = self.state.load()
        service = self._service(record)
        marker = self._marker(command)
        active = self.list_active(service, record["tasklist_id"])
        return service, record, [item for item in active if item.get("notes") == marker]

    def create_once(self, command: EffectCommand) -> None:
        with self.lock:
            service, record, matches = self.matching(command)
            active = self.list_active(service, record["tasklist_id"])
            unrelated = [item for item in active if item.get("notes") != self._marker(command)]
            if unrelated:
                raise HTTPException(409, "dedicated sandbox list is not empty")
            if len(matches) > 1:
                raise HTTPException(409, "multiple sandbox effects observed")
            if not matches:
                service.tasks().insert(
                    tasklist=record["tasklist_id"],
                    body={
                        "title": "PU Workspace S10 sink-only effect",
                        "notes": self._marker(command),
                    },
                ).execute()

    def lookup(self, command: Command) -> dict[str, Any]:
        with self.lock:
            _service, _record, matches = self.matching(command)
        if not matches:
            return {"outcome": "UNKNOWN", "observed_effects": 0}
        return {"outcome": "APPLIED", "observed_effects": len(matches), **command.model_dump()}

    def cleanup(self, command: Command) -> None:
        with self.lock:
            service, record, matches = self.matching(command)
            for item in matches:
                service.tasks().delete(tasklist=record["tasklist_id"], task=item["id"]).execute()
            if self.list_active(service, record["tasklist_id"]):
                raise HTTPException(409, "dedicated sandbox list is not empty after cleanup")

    def cleanup_expired(self) -> int:
        """Delete only bridge-created tasks whose creation time exceeds the TTL."""
        with self.lock:
            record = self.state.load()
            if not record.get("credentials") or not record.get("tasklist_id"):
                return 0
            service = self._service(record)
            now = time.time()
            deleted = 0
            for item in self.list_active(service, record["tasklist_id"]):
                notes = item.get("notes", "")
                updated = item.get("updated", "")
                if not notes.startswith("puw-s10:") or not updated:
                    continue
                from datetime import datetime

                age = now - datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
                if age >= self.state.settings.cleanup_ttl_seconds:
                    service.tasks().delete(tasklist=record["tasklist_id"], task=item["id"]).execute()
                    deleted += 1
            return deleted


def _client_config(settings: Settings) -> dict[str, Any]:
    return {
        "web": {
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.redirect_uri],
        }
    }


def create_app(settings: Settings | None = None, sink: GoogleTasksSink | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = EncryptedState(settings)
    sink = sink or GoogleTasksSink(state)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async def sweep_expired() -> None:
            interval = min(60, settings.cleanup_ttl_seconds)
            while True:
                await asyncio.sleep(interval)
                try:
                    await asyncio.to_thread(sink.cleanup_expired)
                except Exception:
                    # The next sweep retries; provider diagnostics must not leak.
                    pass

        sweeper = asyncio.create_task(sweep_expired())
        try:
            yield
        finally:
            sweeper.cancel()
            try:
                await sweeper
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="PU Workspace S10 live-provider bridge",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    def authorize(
        authorization: str = Header(default=""),
        run_nonce: str = Header(default="", alias="X-PUW-Run-Nonce"),
    ) -> str:
        expected = f"Bearer {settings.bridge_token}"
        if not hmac.compare_digest(authorization, expected) or not __import__("re").fullmatch(HEX64, run_nonce):
            raise HTTPException(401, "unauthorized")
        return run_nonce

    def require_command(command: Command, run_nonce: str) -> None:
        if not hmac.compare_digest(command.run_nonce, run_nonce):
            raise HTTPException(403, "run nonce mismatch")
        if not hmac.compare_digest(command.account_fingerprint, sink.account_fingerprint()):
            raise HTTPException(403, "account fingerprint mismatch")

    @app.get("/healthz")
    def healthz():
        return {"ready": sink.configured()}

    @app.post("/oauth/start")
    def oauth_start(setup_token: str = Header(default="", alias="X-PUW-Setup-Token")):
        if not hmac.compare_digest(setup_token, settings.setup_token):
            raise HTTPException(404, "not found")
        if sink.configured():
            raise HTTPException(409, "sandbox identity is already configured")
        nonce = secrets.token_urlsafe(32)
        record = state.load()
        record["oauth_state"] = hashlib.sha256(nonce.encode()).hexdigest()
        record["oauth_state_expires_at"] = int(time.time()) + 600
        state.save(record)
        flow = Flow.from_client_config(_client_config(settings), scopes=OAUTH_SCOPES, redirect_uri=settings.redirect_uri)
        url, _ = flow.authorization_url(
            state=nonce,
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
        return RedirectResponse(url, status_code=303)

    @app.get("/oauth/callback", response_class=HTMLResponse)
    @app.get("/projects/storage/google/callback", response_class=HTMLResponse, include_in_schema=False)
    def oauth_callback(state_value: str = Query(alias="state"), code: str = Query()):
        record = state.load()
        state_hash = hashlib.sha256(state_value.encode()).hexdigest()
        if (
            not record.get("oauth_state")
            or not hmac.compare_digest(record["oauth_state"], state_hash)
            or int(record.get("oauth_state_expires_at", 0)) < int(time.time())
        ):
            raise HTTPException(400, "invalid or expired OAuth state")
        record.pop("oauth_state", None)
        record.pop("oauth_state_expires_at", None)
        state.save(record)  # consume before exchanging the code
        flow = Flow.from_client_config(_client_config(settings), scopes=OAUTH_SCOPES, state=state_value,
                                       redirect_uri=settings.redirect_uri)
        flow.fetch_token(code=code)
        service = build("oauth2", "v2", credentials=flow.credentials, cache_discovery=False)
        identity = service.userinfo().get().execute()
        email = str(identity.get("email", "")).casefold()
        if email != settings.expected_email or identity.get("verified_email") is not True or not identity.get("id"):
            raise HTTPException(403, "unexpected or unverified Google identity")
        tasks = build("tasks", "v1", credentials=flow.credentials, cache_discovery=False)
        lists = tasks.tasklists().list(maxResults=100).execute().get("items", [])
        matches = [item for item in lists if item.get("title") == TASK_LIST_TITLE]
        if len(matches) > 1:
            raise HTTPException(409, "multiple dedicated sandbox task lists exist")
        tasklist_id = matches[0]["id"] if matches else tasks.tasklists().insert(body={"title": TASK_LIST_TITLE}).execute()["id"]
        if GoogleTasksSink(state).list_active(tasks, tasklist_id):
            raise HTTPException(409, "dedicated sandbox list must be empty")
        state.save({
            "credentials": json.loads(flow.credentials.to_json()),
            "account_fingerprint": hashlib.sha256(str(identity["id"]).encode()).hexdigest(),
            "tasklist_id": tasklist_id,
            "connected_at": int(time.time()),
        })
        return HTMLResponse("<h1>S10 sandbox connected</h1><p>You may close this window.</p>")

    @app.get("/v1/acceptance/capabilities")
    def capabilities(run_nonce: str = Depends(authorize)):
        return {
            "schema": CAPABILITIES_SCHEMA,
            "environment": "ephemeral-test",
            "effect_class": "sink-only",
            "address_policy": "no-external-delivery",
            "cleanup": "supported",
            "fault": "timeout-after-effect",
            "account_fingerprint": sink.account_fingerprint(),
            "run_nonce": run_nonce,
        }

    @app.post("/v1/acceptance/effects")
    def effects(command: EffectCommand, run_nonce: str = Depends(authorize)):
        require_command(command, run_nonce)
        sink.cleanup_expired()
        sink.create_once(command)
        raise HTTPException(504, "expected timeout after sink effect")

    @app.post("/v1/acceptance/lookup")
    def lookup(command: Command, run_nonce: str = Depends(authorize)):
        require_command(command, run_nonce)
        return sink.lookup(command)

    @app.post("/v1/acceptance/cleanup")
    def cleanup(command: Command, run_nonce: str = Depends(authorize)):
        require_command(command, run_nonce)
        sink.cleanup(command)
        return {"status": "CLEANED"}

    return app


def app_factory() -> FastAPI:
    return create_app()

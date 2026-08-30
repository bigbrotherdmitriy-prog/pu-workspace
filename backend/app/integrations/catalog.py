from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.integrations.ai import configured_ai_provider
from app.integrations.google_workspace import google_workspace_for_project
from app.integrations.telegram import TelegramChannelAdapter


GOOGLE_CAPABILITIES = (
    ("storage", "Google Drive", "Документы и рабочие папки", "https://www.googleapis.com/auth/drive"),
    ("task", "Google Tasks", "Подтверждённые задачи", "https://www.googleapis.com/auth/tasks"),
    ("calendar", "Google Calendar", "Сроки и события проекта", "https://www.googleapis.com/auth/calendar.events"),
    ("channel", "Gmail", "Входящие письма и подтверждаемая отправка", "https://www.googleapis.com/auth/gmail.readonly"),
)


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    key: str
    provider: str
    capability: str
    name: str
    description: str
    available: bool
    connected: bool
    action: str | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def project_integration_catalog(project_id: int, db: Session) -> list[IntegrationStatus]:
    """Resolve integration status outside Core and without vendor models in the API layer."""
    google = google_workspace_for_project(project_id, db)
    google_health = google.health()
    google_scopes = google.authorized_scopes()
    result = [
        IntegrationStatus(
            key=f"google_workspace:{capability}",
            provider="google_workspace",
            capability=capability,
            name=name,
            description=description,
            available=google.configured(),
            connected=google_health.ready and scope in google_scopes,
            action=(
                "sync" if capability == "channel"
                else "select_source" if capability == "storage" and google_health.ready and scope in google_scopes
                else "oauth"
            ),
            detail="scope granted" if google_health.ready and scope in google_scopes else (
                "authorization required" if google.configured() else "provider is not configured"
            ),
        )
        for capability, name, description, scope in GOOGLE_CAPABILITIES
    ]

    telegram = TelegramChannelAdapter().health()
    ai_provider = configured_ai_provider()
    ai = ai_provider.health()
    result.extend(
        [
            IntegrationStatus(
                key="telegram:channel",
                provider="telegram",
                capability="channel",
                name="Telegram",
                description="Сообщения, файлы и уведомления",
                available=True,
                connected=telegram.ready,
                detail=telegram.detail,
            ),
            IntegrationStatus(
                key="local:storage",
                provider="local",
                capability="storage",
                name="Локальная рабочая папка",
                description="Безопасная загрузка файлов с компьютера",
                available=True,
                connected=True,
                action="local_upload",
                detail="ready",
            ),
            IntegrationStatus(
                key=f"{ai_provider.provider}:ai",
                provider=ai_provider.provider,
                capability="ai",
                name="AI-анализ",
                description="AIProviderAdapter с политикой защиты данных",
                available=True,
                connected=ai.ready,
                action="ai_policy",
                detail=ai.detail,
            ),
        ]
    )
    return result

import os

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import require_project_role, require_user
from app.database import get_db
from app.integrations.ai import configured_ai_provider
from app.integrations.telegram import TelegramChannelAdapter
from app.models.google_token import GoogleOAuthToken
from app.models.user import User

router = APIRouter(prefix="/integrations", tags=["integrations"])

GOOGLE_CAPABILITIES = (
    ("storage", "Google Drive", "Документы и рабочие папки", "https://www.googleapis.com/auth/drive"),
    ("task", "Google Tasks", "Подтверждённые задачи", "https://www.googleapis.com/auth/tasks"),
    ("calendar", "Google Calendar", "Сроки и события проекта", "https://www.googleapis.com/auth/calendar.events"),
    ("channel", "Gmail", "Входящие письма и подтверждаемая отправка", "https://www.googleapis.com/auth/gmail.readonly"),
)


@router.get("/project")
def project_integrations(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    require_project_role(db, user, project_id, "viewer")
    token = db.scalar(select(GoogleOAuthToken).where(GoogleOAuthToken.project_id == project_id))
    scopes = set((token.scopes or "").split()) if token else set()
    google_configured = bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))
    adapters = [
        {
            "key": f"google_workspace:{capability}",
            "provider": "google_workspace",
            "capability": capability,
            "name": name,
            "description": description,
            "available": google_configured,
            "connected": bool(token and token.access_token and scope in scopes),
            "action": "sync" if name == "Gmail" else "oauth",
        }
        for capability, name, description, scope in GOOGLE_CAPABILITIES
    ]
    telegram = TelegramChannelAdapter().health()
    ai = configured_ai_provider().health()
    adapters.extend([
        {
            "key": "telegram:channel", "provider": "telegram", "capability": "channel",
            "name": "Telegram", "description": "Сообщения, файлы и уведомления",
            "available": True, "connected": telegram.ready, "action": None, "detail": telegram.detail,
        },
        {
            "key": "local:storage", "provider": "local", "capability": "storage",
            "name": "Локальная рабочая папка", "description": "Безопасная загрузка файлов с компьютера",
            "available": True, "connected": True, "action": "local_upload", "detail": "ready",
        },
        {
            "key": "gemini:ai", "provider": "gemini", "capability": "ai",
            "name": "AI-анализ", "description": "Текущий AIProviderAdapter с политикой защиты данных",
            "available": True, "connected": ai.ready, "action": "ai_policy", "detail": ai.detail,
        },
    ])
    return {"project_id": project_id, "adapters": adapters}

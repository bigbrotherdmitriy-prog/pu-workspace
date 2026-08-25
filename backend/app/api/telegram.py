from __future__ import annotations
import hmac
import os
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from app.core.notifications import notify_telegram_chat
from app.database import SessionLocal
from app.models.project import Project
from app.models.telegram_chat import TelegramChatLink
from app.organizer_engine.types import DriveFile
from app.response_engine import create_response_drafts
from app.task_engine import create_tasks_from_files

router = APIRouter(prefix="/telegram", tags=["telegram"])


def _authorized_admin(user_id: int) -> bool:
    expected = os.getenv("TELEGRAM_ADMIN_USER_ID", "")
    return bool(expected and hmac.compare_digest(str(user_id), expected))


@router.post("/webhook")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret or not x_telegram_bot_api_secret_token or not hmac.compare_digest(secret, x_telegram_bot_api_secret_token):
        raise HTTPException(403, "Invalid Telegram webhook secret")
    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = sender.get("id")
    text = (message.get("text") or message.get("caption") or "").strip()
    if chat_id is None or not text:
        return {"ok": True}

    db = SessionLocal()
    try:
        if text.startswith("/connect"):
            if not _authorized_admin(user_id):
                notify_telegram_chat(chat_id, "Подключить чат может только владелец PU Workspace.")
                return {"ok": True}
            parts = text.split()
            if len(parts) != 2 or not parts[1].isdigit():
                notify_telegram_chat(chat_id, "Использование: /connect ID_ПРОЕКТА")
                return {"ok": True}
            project_id = int(parts[1])
            project = db.scalar(select(Project).where(Project.id == project_id))
            if not project:
                notify_telegram_chat(chat_id, "Проект с таким ID не найден.")
                return {"ok": True}
            link = db.get(TelegramChatLink, chat_id)
            if link:
                link.project_id = project_id; link.title = chat.get("title") or chat.get("username") or "Telegram"; link.enabled = True
            else:
                db.add(TelegramChatLink(chat_id=chat_id, project_id=project_id, title=chat.get("title") or chat.get("username") or "Telegram"))
            db.commit()
            notify_telegram_chat(chat_id, f"✅ Чат подключён к проекту «{project.name}». Новые сообщения будут анализироваться.")
            return {"ok": True}

        if text.startswith("/disconnect") and _authorized_admin(user_id):
            link = db.get(TelegramChatLink, chat_id)
            if link:
                link.enabled = False; db.commit()
            notify_telegram_chat(chat_id, "Чат отключён от анализа PU Workspace.")
            return {"ok": True}

        if text.startswith("/help") or text.startswith("/start"):
            notify_telegram_chat(chat_id, "PU Workspace: /connect ID — подключить рабочий чат; /disconnect — отключить. В подключённых чатах поручения превращаются в задачи, запросы — в черновики ответов.")
            return {"ok": True}

        link = db.get(TelegramChatLink, chat_id)
        if not link or not link.enabled:
            return {"ok": True}
        synthetic = DriveFile(id=f"telegram:{chat_id}:{message.get('message_id')}", name=f"Telegram — {link.title}", mime_type="text/plain", parent_id="telegram", content_text=text)
        tasks = create_tasks_from_files(db, link.project_id, None, [synthetic], source_type="telegram")
        drafts = create_response_drafts(db, link.project_id, None, [synthetic])
        if tasks or drafts:
            notify_telegram_chat(chat_id, f"PU Workspace: создано задач — {len(tasks)}, черновиков ответов — {len(drafts)}.")
        return {"ok": True}
    finally:
        db.close()

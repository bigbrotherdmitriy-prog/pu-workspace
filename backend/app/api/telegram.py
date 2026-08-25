from __future__ import annotations
import hmac
import os
import httpx
from datetime import date, datetime, timezone
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from app.core.notifications import notify_telegram_chat, telegram_http_client
from app.database import SessionLocal
from app.models.project import Project
from app.models.telegram_chat import TelegramChatLink
from app.models.task import Task, TaskDueDateHistory
from app.models.project_member import ProjectMember
from app.models.user import User
from app.organizer_engine.types import DriveFile
from app.organizer_engine.content import extract_text
from app.response_engine import create_response_drafts
from app.task_engine import create_tasks_from_files
from app.google_tasks import sync_tasks_to_google
from app.google_calendar import sync_tasks_to_calendar
from app.summary_engine import brief_summary

router = APIRouter(prefix="/telegram", tags=["telegram"])


def _authorized_admin(user_id: int) -> bool:
    expected = os.getenv("TELEGRAM_ADMIN_USER_ID", "")
    return bool(expected and hmac.compare_digest(str(user_id), expected))


def _parse_ru_date(value: str) -> date:
    return datetime.strptime(value, "%d.%m.%Y").date()


def _project_owner(db, project_id: int) -> User | None:
    return db.scalar(select(User).join(ProjectMember, ProjectMember.user_id == User.id).where(ProjectMember.project_id == project_id).order_by((ProjectMember.role == "owner").desc(), User.id))


def _download_document(document: dict) -> tuple[str, str, bytes]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Telegram bot token is not configured")
    size = int(document.get("file_size") or 0)
    if size > 20 * 1024 * 1024:
        raise ValueError("Файл больше 20 МБ; Telegram Bot API не позволяет безопасно скачать его этим способом")
    file_id = document.get("file_id")
    if not file_id:
        raise ValueError("Telegram file_id missing")
    relay = os.getenv("TELEGRAM_RELAY_URL", "")
    relay_secret = os.getenv("TELEGRAM_RELAY_SECRET", "")
    if relay and relay_secret:
        response = httpx.get(f"{relay.rstrip('/')}/file/{file_id}", headers={"X-Relay-Secret": relay_secret}, timeout=35.0)
        response.raise_for_status()
    else:
        with telegram_http_client(timeout=30.0) as client:
            meta = client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id})
            meta.raise_for_status()
            path = meta.json()["result"]["file_path"]
            response = client.get(f"https://api.telegram.org/file/bot{token}/{path}")
            response.raise_for_status()
    if len(response.content) > 20 * 1024 * 1024:
        raise ValueError("Скачанный файл превышает лимит 20 МБ")
    return document.get("file_name") or "telegram-file", document.get("mime_type") or "application/octet-stream", response.content


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
    document = message.get("document")
    if chat_id is None:
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
            notify_telegram_chat(chat_id, "PU Workspace: /connect ID — подключить чат; /disconnect — отключить; /tasks — открытые задачи; /take ID — принять; /done ID результат — выполнить; /move ID ДД.ММ.ГГГГ причина — перенести срок.")
            return {"ok": True}

        link = db.get(TelegramChatLink, chat_id)
        if not link or not link.enabled:
            if document:
                notify_telegram_chat(chat_id, "Этот чат ещё не подключён к проекту. Владелец должен отправить: /connect 1")
            return {"ok": True}
        if text.startswith("/tasks"):
            rows = list(db.scalars(select(Task).where(Task.project_id == link.project_id, Task.status.in_(["assigned", "in_progress"])).order_by(Task.due_date.asc().nullslast(), Task.id).limit(20)).all())
            if not rows:
                notify_telegram_chat(chat_id, "Открытых задач нет.")
            else:
                lines = ["📋 Кто что должен:"]
                for task in rows:
                    due = task.due_date.strftime("%d.%m.%Y") if task.due_date else "без срока"
                    status = "в работе" if task.status == "in_progress" else "назначено"
                    lines.append(f"#{task.id} · {due} · {status}\n{task.title[:180]}")
                lines.append("Команды: /take ID · /done ID результат · /move ID ДД.ММ.ГГГГ причина")
                notify_telegram_chat(chat_id, "\n\n".join(lines))
            return {"ok": True}
        if text.startswith(("/take", "/done", "/move")):
            if not _authorized_admin(user_id):
                notify_telegram_chat(chat_id, "Изменять задачи через Telegram пока может только владелец PU Workspace.")
                return {"ok": True}
            parts = text.split(maxsplit=3)
            if len(parts) < 2 or not parts[1].isdigit():
                notify_telegram_chat(chat_id, "Укажите ID задачи. Пример: /take 15")
                return {"ok": True}
            task = db.get(Task, int(parts[1]))
            if not task or task.project_id != link.project_id:
                notify_telegram_chat(chat_id, "Задача не найдена в этом проекте.")
                return {"ok": True}
            owner = _project_owner(db, link.project_id)
            if not owner:
                notify_telegram_chat(chat_id, "У проекта нет владельца для записи изменения.")
                return {"ok": True}
            if text.startswith("/take"):
                task.status = "in_progress"
                answer = f"▶️ Задача #{task.id} принята в работу."
            elif text.startswith("/done"):
                result = text.split(maxsplit=2)[2].strip() if len(text.split(maxsplit=2)) > 2 else ""
                if not result:
                    notify_telegram_chat(chat_id, "Укажите результат: /done ID что выполнено и где подтверждение")
                    return {"ok": True}
                task.status = "completed"; task.result_note = result; task.completed_at = datetime.now(timezone.utc)
                answer = f"✅ Задача #{task.id} выполнена. Результат сохранён."
            else:
                if len(parts) < 4:
                    notify_telegram_chat(chat_id, "Формат: /move ID ДД.ММ.ГГГГ причина переноса")
                    return {"ok": True}
                try:
                    new_due = _parse_ru_date(parts[2])
                except ValueError:
                    notify_telegram_chat(chat_id, "Дата должна быть в формате ДД.ММ.ГГГГ.")
                    return {"ok": True}
                db.add(TaskDueDateHistory(task_id=task.id, old_due_date=task.due_date, new_due_date=new_due, reason=parts[3].strip(), changed_by_user_id=owner.id))
                task.due_date = new_due
                answer = f"📅 Срок задачи #{task.id} перенесён на {parts[2]}. Причина сохранена."
            db.commit(); db.refresh(task)
            sync_tasks_to_google(db, link.project_id, [task], force_update=True)
            sync_tasks_to_calendar(db, link.project_id, [task], force_update=True)
            notify_telegram_chat(chat_id, answer)
            return {"ok": True}
        source_name = f"Telegram — {link.title}"
        source_id = f"telegram:{chat_id}:{message.get('message_id')}"
        mime_type = "text/plain"
        content = text
        if document:
            try:
                filename, mime_type, data = _download_document(document)
                extracted = extract_text(data, mime_type, filename)
            except Exception as exc:
                notify_telegram_chat(chat_id, f"Не удалось обработать файл: {str(exc)[:500]}")
                return {"ok": True}
            if not extracted:
                notify_telegram_chat(chat_id, "В файле не найден машиночитаемый текст. Возможно, это скан — для него потребуется OCR.")
                return {"ok": True}
            source_name = filename
            source_id = f"telegram-file:{chat_id}:{document.get('file_unique_id') or document.get('file_id')}"
            content = (text + "\n" + extracted).strip()
            notify_telegram_chat(chat_id, f"Файл «{filename}» получен, текст извлечён. Выполняю анализ.")
        if not content:
            return {"ok": True}
        synthetic = DriveFile(id=source_id, name=source_name, mime_type=mime_type, parent_id="telegram", content_text=content)
        tasks = create_tasks_from_files(db, link.project_id, None, [synthetic], source_type="telegram")
        google_synced, _ = sync_tasks_to_google(db, link.project_id, tasks)
        calendar_synced, _ = sync_tasks_to_calendar(db, link.project_id, tasks)
        drafts = create_response_drafts(db, link.project_id, None, [synthetic])
        if document:
            notify_telegram_chat(chat_id, brief_summary(content, source_name, len(tasks), len(drafts), calendar_synced))
        elif tasks or drafts:
            notify_telegram_chat(chat_id, f"PU Workspace: задач — {len(tasks)}, Google Tasks — {google_synced}, Calendar — {calendar_synced}, черновиков ответов — {len(drafts)}.")
        return {"ok": True}
    finally:
        db.close()

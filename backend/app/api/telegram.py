from __future__ import annotations
import hmac
import os
import time
import httpx
from datetime import date, datetime, timezone
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from app.integrations.telegram import notify_telegram_chat, telegram_http_client
from app.database import SessionLocal
from app.models.project import Project
from app.models.document import Document
from app.models.telegram_chat import TelegramChatLink
from app.models.task import Task, TaskDueDateHistory
from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.ai_secretary import Message
from app.models.response_draft import ResponseDraft
from app.models.audit_log import AuditLog
from app.organizer_engine.types import DriveFile
from app.organizer_engine.content import extract_text
from app.response_engine import create_response_drafts
from app.task_engine import create_tasks_from_files
from app.summary_engine import brief_summary
from app.governance_engine import create_governance_items
from app.document_engine import index_documents
from app.gemini_analysis import format_gemini_analysis, format_message_replies
from app.integrations.ai import configured_ai_provider
from app.api.ai_secretary import _contract_candidate
from app.ai_policy import ExternalAIBlocked, policy_for_project, prepare_external_ai_text
from app.ai_cache import cached_ai_result

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
        response = None
        for attempt in range(3):
            try:
                response = httpx.get(f"{relay.rstrip('/')}/file/{file_id}", headers={"X-Relay-Secret": relay_secret}, timeout=35.0)
                response.raise_for_status()
                break
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise RuntimeError("Telegram временно не отдал файл после трёх попыток") from exc
                time.sleep(attempt + 1)
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


def _incoming_file(message: dict) -> dict | None:
    document = message.get("document")
    if document:
        return document
    photos = message.get("photo") or []
    if not photos:
        return None
    photo = dict(photos[-1])
    photo.setdefault("file_name", f"telegram-photo-{message.get('message_id', 'unknown')}.jpg")
    photo.setdefault("mime_type", "image/jpeg")
    return photo


def _public_download_error(_: Exception) -> str:
    return "Не удалось скачать файл из Telegram. Попробуйте отправить его ещё раз через несколько секунд."


def _empty_extraction_message(mime_type: str, has_caption: bool) -> str:
    if mime_type.startswith("image/") and has_caption:
        return "Текст внутри изображения распознать не удалось. Анализирую текст из подписи."
    if mime_type.startswith("image/"):
        return (
            "Локальный OCR не смог распознать текст на изображении. "
            "Отправьте более чёткое фото без бликов либо документ PDF/DOCX."
        )
    return (
        "В файле не найден машиночитаемый текст, а локальный OCR не дал результата. "
        "Попробуйте более качественный скан или исходный PDF/DOCX."
    )


def _should_prepare_message_replies(message: dict, text: str) -> bool:
    if not text or text.startswith("/"):
        return False
    if (message.get("chat") or {}).get("type") == "private":
        return True
    return bool(
        message.get("forward_origin")
        or message.get("forward_from")
        or message.get("forward_sender_name")
        or message.get("forward_from_chat")
    )


def _store_ai_message_result(
    db, inbox_message: Message, drafts: list[ResponseDraft], result: dict,
    owner: User, synthetic: DriveFile,
) -> list[ResponseDraft]:
    """Persist the useful AI result instead of only sending it to Telegram."""
    summary = str(result.get("message_summary") or "").strip()
    action = str(result.get("recommended_action") or "").strip()
    inbox_message.summary = "\n".join(
        part for part in (
            f"🧠 Анализ сообщения: {summary}" if summary else "",
            f"➡️ Рекомендация: {action}" if action else "",
        ) if part
    ) or inbox_message.summary

    body = str(result.get("business_reply") or result.get("short_reply") or "").strip()
    if not body:
        return drafts
    confidence = {"high": 0.9, "medium": 0.75, "low": 0.55}.get(result.get("confidence"), 0.65)
    if drafts:
        drafts[0].body = body
        drafts[0].confidence = confidence
        return drafts

    # The local extractor can legitimately find no explicit request. Gemini may
    # still determine that a reply is useful, so keep one reviewable draft.
    import hashlib
    digest = hashlib.sha256(("telegram-ai:" + body).casefold().encode()).hexdigest()
    draft = ResponseDraft(
        project_id=inbox_message.project_id,
        reviewer_user_id=owner.id,
        message_id=inbox_message.id,
        subject=f"Ответ на сообщение из «{inbox_message.source_name}»"[:500],
        body=body,
        source_file_id=synthetic.id,
        source_file_name=synthetic.name,
        source_excerpt=inbox_message.content[:1200],
        source_excerpt_hash=digest,
        confidence=confidence,
    )
    db.add(draft)
    drafts.append(draft)
    return drafts


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
    document = _incoming_file(message)
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
            if task.external_action_status in {"approved", "queued", "executed"}:
                task.external_action_status = "proposed"
                task.record_version = int(task.record_version or 1) + 1
                db.commit()
            notify_telegram_chat(chat_id, answer)
            return {"ok": True}
        source_name = f"Telegram — {link.title}"
        source_id = f"telegram:{chat_id}:{message.get('message_id')}"
        mime_type = "text/plain"
        content = text
        if document:
            source_id = f"telegram-file:{chat_id}:{document.get('file_unique_id') or document.get('file_id')}"
            already_processed = db.scalar(select(Document.id).where(
                Document.project_id == link.project_id,
                Document.external_id == source_id,
            ))
            if already_processed:
                notify_telegram_chat(chat_id, "Этот файл уже был проанализирован ранее — повторные задачи и риски не созданы.")
                return {"ok": True}
            try:
                filename, mime_type, data = _download_document(document)
                extracted = extract_text(data, mime_type, filename)
            except Exception as exc:
                notify_telegram_chat(chat_id, _public_download_error(exc))
                return {"ok": True}
            if not extracted:
                notify_telegram_chat(chat_id, _empty_extraction_message(mime_type, bool(text)))
                if not (mime_type.startswith("image/") and text):
                    return {"ok": True}
            source_name = filename
            content = (text + "\n" + extracted).strip()
            notify_telegram_chat(chat_id, f"Файл «{filename}» получен, текст извлечён. Выполняю анализ.")
        if not content:
            return {"ok": True}
        existing_message = db.scalar(select(Message).where(Message.source_type == "telegram", Message.source_external_id == source_id))
        if existing_message:
            notify_telegram_chat(chat_id, "Это сообщение уже сохранено во входящих AI Secretary.")
            return {"ok": True}
        project = db.get(Project, link.project_id)
        owner = _project_owner(db, link.project_id)
        if project is None or owner is None:
            notify_telegram_chat(chat_id, "Не удалось определить проект или его владельца.")
            return {"ok": True}
        contract, context_confidence, context_evidence = _contract_candidate(db, link.project_id, content)
        inbox_message = Message(
            organization_id=project.organization_id, project_id=project.id,
            contract_id=contract.id if contract else None, created_by_user_id=owner.id,
            source_type="telegram", source_external_id=source_id, source_name=source_name,
            source_url=f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message.get('message_id')}" if str(chat_id).startswith("-100") else None,
            content=content, summary="Анализируется", context_confidence=context_confidence,
            context_evidence="Проект подтверждён подключением Telegram-чата. " + context_evidence,
            context_confirmed=True, status="ready",
        )
        db.add(inbox_message); db.flush()
        synthetic = DriveFile(id=f"message:{inbox_message.id}", name=source_name, mime_type=mime_type, parent_id="telegram", content_text=content)
        index_documents(db, link.project_id, [synthetic], "telegram")
        tasks = create_tasks_from_files(db, link.project_id, None, [synthetic], source_type="telegram")
        google_synced = calendar_synced = 0
        prepare_replies = _should_prepare_message_replies(message, text)
        drafts = create_response_drafts(
            db, link.project_id, None, [synthetic], ensure_response=prepare_replies,
        )
        risks, decisions = create_governance_items(db, link.project_id, [synthetic], source_type="telegram")
        for task in tasks:
            task.message_id = inbox_message.id
            task.external_action_status = "proposed"
        for draft in drafts:
            draft.message_id = inbox_message.id
        inbox_message.summary = brief_summary(content, source_name, len(tasks), len(drafts), 0)
        if document:
            ai_provider = configured_ai_provider()
            if ai_provider.health().ready:
                try:
                    ai_content, ai_mode = prepare_external_ai_text(db, link.project_id, content)
                    policy = policy_for_project(db, link.project_id)
                    prompt_version = policy.prompt_version if policy else "v1"
                    semantic, cache_hit = cached_ai_result(
                        db, provider=ai_provider.provider, model=ai_provider.model,
                        operation="document_analysis", prompt_version=prompt_version,
                        policy_mode=ai_mode, text=ai_content, context=source_name,
                        compute=lambda: ai_provider.analyze_document(ai_content, source_name),
                    )
                    summary = format_gemini_analysis(semantic, source_name)
                    inbox_message.summary = summary
                    db.add(AuditLog(action="external_ai_used", entity_type="message", entity_id=inbox_message.id,
                                    details=f"provider={ai_provider.provider}; model={ai_provider.model}; mode={ai_mode}; prompt={prompt_version}; cache_hit={str(cache_hit).lower()}"))
                except ExternalAIBlocked:
                    summary = "ℹ️ Внешний AI отключён политикой проекта. Ниже локальная сводка.\n\n" + brief_summary(content, source_name, len(tasks), len(drafts), calendar_synced)
                except Exception:
                    summary = "⚠️ Gemini временно недоступен. Ниже резервная локальная сводка.\n\n" + brief_summary(content, source_name, len(tasks), len(drafts), calendar_synced)
            else:
                summary = "⚠️ Gemini API ещё не настроен. Ниже резервная локальная сводка.\n\n" + brief_summary(content, source_name, len(tasks), len(drafts), calendar_synced)
            notify_telegram_chat(chat_id, summary + f"\n\nПредложено: задач {len(tasks)} · рисков {len(risks)} · решений {len(decisions)}. Внешние действия требуют подтверждения в web.")
        elif prepare_replies and configured_ai_provider().health().ready:
            notify_telegram_chat(chat_id, "⏳ Сообщение получено. Готовлю анализ и варианты ответа…")
            try:
                ai_content, ai_mode = prepare_external_ai_text(db, link.project_id, content)
                ai_provider = configured_ai_provider()
                policy = policy_for_project(db, link.project_id)
                prompt_version = policy.prompt_version if policy else "v1"
                replies, cache_hit = cached_ai_result(
                    db, provider=ai_provider.provider, model=ai_provider.model,
                    operation="message_replies", prompt_version=prompt_version,
                    policy_mode=ai_mode, text=ai_content, context=source_name,
                    compute=lambda: ai_provider.analyze_message(ai_content, source_name),
                )
                drafts = _store_ai_message_result(db, inbox_message, drafts, replies, owner, synthetic)
                notify_telegram_chat(chat_id, format_message_replies(replies))
                db.add(AuditLog(action="external_ai_used", entity_type="message", entity_id=inbox_message.id,
                                details=f"provider={ai_provider.provider}; model={ai_provider.model}; mode={ai_mode}; prompt={prompt_version}; cache_hit={str(cache_hit).lower()}"))
            except ExternalAIBlocked:
                notify_telegram_chat(chat_id, "ℹ️ Внешний AI отключён политикой проекта. Сообщение и локальные предложения сохранены в PU Workspace.")
            except Exception:
                notify_telegram_chat(chat_id, "⚠️ Не удалось подготовить варианты ответа через Gemini. Сообщение сохранено, попробуйте ещё раз позже.")
        elif tasks or drafts or risks or decisions:
            notify_telegram_chat(chat_id, f"PU Workspace: задач — {len(tasks)}, Google Tasks — {google_synced}, Calendar — {calendar_synced}, рисков — {len(risks)}, решений — {len(decisions)}, черновиков ответов — {len(drafts)}.")
        db.add(AuditLog(action="message_processed", entity_type="message", entity_id=inbox_message.id,
                        details=f"source=telegram; tasks={len(tasks)}; drafts={len(drafts)}; risks={len(risks)}"))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.knowledge import AnswerProvider
from app.storage import Lead, Storage
from app.telegram import TelegramClient, TelegramError


def main_menu() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Что такое PU Workspace", "callback_data": "about"}],
            [
                {"text": "Возможности", "callback_data": "capabilities"},
                {"text": "Кому подходит", "callback_data": "audience"},
            ],
            [
                {"text": "Материалы", "callback_data": "materials"},
                {"text": "Написать нам", "callback_data": "contact_us"},
            ],
            [
                {"text": "🎬 Посмотреть демо", "url": "https://puworkspace.ru/materials.html"},
                {"text": "💻 Разработка", "callback_data": "development"},
            ],
            [{"text": "🚀 Получить Early Access", "callback_data": "early_access"}],
        ]
    }


STATUS_LABELS = {
    "new": "Новая",
    "contacted": "Связались",
    "pilot": "Пилот",
    "closed": "Закрыта",
    "rejected": "Отказ",
}

STATUS_ICONS = {
    "new": "🟡",
    "contacted": "📞",
    "pilot": "🚀",
    "closed": "✅",
    "rejected": "⛔",
}


@dataclass
class SalesBotService:
    settings: Settings
    storage: Storage
    telegram: TelegramClient
    answers: AnswerProvider

    def handle_update(self, update: dict[str, Any]) -> None:
        update_id = int(update["update_id"])
        if self.storage.is_update_processed(update_id):
            return
        if callback := update.get("callback_query"):
            self._handle_callback(callback)
        elif message := update.get("message"):
            self._handle_message(message)
        self.storage.mark_update_processed(update_id)

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        self.telegram.answer_callback(callback["id"])
        message = callback.get("message", {})
        chat_id = int(message["chat"]["id"])
        user = callback["from"]
        action = callback.get("data", "")
        if action.startswith("lead:"):
            self._handle_lead_status_callback(chat_id, user, action, message)
            return
        if action == "about":
            text = (
                "<b>PU Workspace</b> — единое рабочее пространство для проектов, договоров, "
                "документов, задач и коммуникаций. AI Secretary понимает контекст, показывает важное "
                "и предлагает действия. Решение находится в Early Access."
            )
        elif action == "capabilities":
            text = (
                "<b>Что даёт PU Workspace</b>\n\n"
                "• единый проектный контекст;\n"
                "• документы и договоры с первоисточниками;\n"
                "• извлечение сроков, сумм, задач и рисков;\n"
                "• проекты ответов и контролируемые действия;\n"
                "• роли, подтверждения и аудит."
            )
        elif action == "audience":
            text = (
                "<b>Первый фокус</b>\n\nСтроительные и проектные компании, подрядчики, "
                "девелоперы и другие организации с большим потоком документов, договоров и переписки."
            )
        elif action == "materials":
            website = self.settings.website_url.rstrip("/")
            links = [f'• <a href="{html.escape(website)}">Сайт PU Workspace</a>']
            links.append(
                f'• <a href="{html.escape(website)}/assets/PU_Workspace_Premium_Brochure.pdf">Премиальный буклет</a>'
            )
            links.append(
                f'• <a href="{html.escape(website)}/assets/PU_Workspace_Provider_Agnostic_Offer.pdf">Расширенное предложение</a>'
            )
            links.append(f'• <a href="{html.escape(website)}/materials.html">Видео и все материалы</a>')
            links.append(f'• <a href="{html.escape(self.settings.channel_url)}">Telegram-канал</a>')
            text = "<b>Материалы</b>\n\n" + "\n".join(links)
        elif action == "contact_us":
            text = (
                "<b>Связаться с PU Workspace</b>\n\n"
                "Почта: <a href=\"mailto:hello@puworkspace.ru\">hello@puworkspace.ru</a>\n"
                "Или оставьте заявку на Early Access — она сразу поступит владельцу проекта."
            )
        elif action == "development":
            self.storage.set_session(
                int(user["id"]), "menu", {"source": "bot_development"}
            )
            development_text = (
                "<b>Разработка цифровых решений</b>\n\n"
                "🌐 Сайты для бизнеса\n"
                "🧩 Личные кабинеты и веб-сервисы\n"
                "🤖 Telegram-боты\n"
                "🔗 Интеграции CRM, ERP и API\n"
                "✨ AI-автоматизация\n\n"
                "Расскажите о задаче — подготовим реалистичный первый этап."
            )
            development_menu = {
                "inline_keyboard": [
                    [{"text": "Посмотреть направление", "url": "https://puworkspace.ru/development.html"}],
                    [{"text": "💻 Заказать разработку", "callback_data": "development_lead"}],
                    [{"text": "Главное меню", "callback_data": "menu"}],
                ]
            }
            self._send_branded(chat_id, development_text, development_menu)
            return
        elif action == "development_lead":
            self.storage.set_session(
                int(user["id"]), "menu", {"source": "bot_development"}
            )
            self.telegram.send_message(
                chat_id,
                "Перед заявкой подтвердите согласие на обработку указанных вами контактных данных "
                "исключительно для обсуждения разработки.",
                {
                    "inline_keyboard": [
                        [{"text": "Согласен, продолжить", "callback_data": "lead_consent"}],
                        [{"text": "Отмена", "callback_data": "menu"}],
                    ]
                },
            )
            return
        elif action == "early_access":
            self.telegram.send_message(
                chat_id,
                "Перед заявкой подтвердите согласие на обработку указанных вами контактных данных "
                "исключительно для связи по Early Access.",
                {
                    "inline_keyboard": [
                        [{"text": "Согласен, продолжить", "callback_data": "lead_consent"}],
                        [{"text": "Отмена", "callback_data": "menu"}],
                    ]
                },
            )
            return
        elif action == "lead_consent":
            session = self.storage.get_session(int(user["id"]))
            source = session[1].get("source", "direct") if session else "direct"
            self.storage.set_session(
                int(user["id"]), "company", {"consent": "accepted", "source": source}
            )
            first_prompt = (
                "Шаг 1 из 5. Как называется ваша компания или проект?"
                if source.startswith("development") or source == "bot_development"
                else "Шаг 1 из 5. Как называется ваша компания?"
            )
            self.telegram.send_message(chat_id, first_prompt)
            return
        elif action == "menu":
            self.storage.clear_session(int(user["id"]))
            self.telegram.send_message(chat_id, "Главное меню", main_menu())
            return
        else:
            text = "Раздел пока недоступен. Вернитесь в главное меню."
        self.telegram.send_message(chat_id, text, main_menu())

    def _handle_message(self, message: dict[str, Any]) -> None:
        chat_id = int(message["chat"]["id"])
        user = message["from"]
        user_id = int(user["id"])
        text = (message.get("text") or "").strip()
        if text.startswith("/start") or text == "/menu":
            source = "direct"
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                if len(parts) == 2:
                    candidate = re.sub(r"[^A-Za-z0-9_-]", "", parts[1])[:64]
                    source = candidate or "direct"
            self.storage.set_session(user_id, "menu", {"source": source})
            caption = (
                "<b>PU Workspace</b>\n"
                "Бизнес, который понимает сам себя.\n\n"
                "AI-секретарь для проектов, договоров, документов и задач. "
                "Посмотрите возможности, демо или оставьте заявку на Early Access."
            )
            if source.startswith("development"):
                caption += "\n\nТакже мы разрабатываем сайты, корпоративные сервисы и автоматизацию."
            if text.startswith("/start"):
                self._send_branded(chat_id, caption, main_menu())
            else:
                self.telegram.send_message(chat_id, caption, main_menu())
            return
        if text == "/cancel":
            self.storage.clear_session(user_id)
            self.telegram.send_message(chat_id, "Заявка отменена.", main_menu())
            return
        if text == "/id":
            self.telegram.send_message(
                chat_id,
                f"Ваш Telegram chat ID: <code>{chat_id}</code>\n"
                "Он нужен только владельцу для получения новых заявок.",
                main_menu(),
            )
            return
        if text.startswith("/leads") or text == "/stats":
            self._handle_admin_command(chat_id, user_id, text)
            return
        session = self.storage.get_session(user_id)
        if session:
            self._advance_lead(chat_id, user, text, message.get("contact"), session)
            return
        if not text:
            self.telegram.send_message(chat_id, "Пожалуйста, отправьте текст или выберите пункт меню.", main_menu())
            return
        self.telegram.send_message(chat_id, self.answers.answer(text), main_menu())

    def _send_branded(
        self, chat_id: int, text: str, reply_markup: dict[str, Any]
    ) -> None:
        try:
            self.telegram.send_photo(
                chat_id,
                f"{self.settings.website_url.rstrip('/')}/assets/product.jpg",
                text,
                reply_markup,
            )
        except TelegramError:
            self.telegram.send_message(chat_id, text, reply_markup)

    def _advance_lead(
        self,
        chat_id: int,
        user: dict[str, Any],
        text: str,
        contact: dict[str, Any] | None,
        session: tuple[str, dict[str, str]],
    ) -> None:
        state, payload = session
        user_id = int(user["id"])
        prompts = {
            "company": ("name", "company", "Шаг 2 из 5. Как к вам обращаться?"),
            "name": ("role", "name", "Шаг 3 из 5. Ваша должность или роль?"),
            "role": ("need", "role", "Шаг 4 из 5. Какую рабочую проблему вы хотите решить?"),
            "need": ("contact", "need", "Шаг 5 из 5. Оставьте телефон, email или Telegram для связи."),
        }
        if state in prompts:
            if not text or len(text) > 2000:
                self.telegram.send_message(chat_id, "Введите ответ текстом (до 2000 символов).")
                return
            next_state, key, prompt = prompts[state]
            payload[key] = text
            self.storage.set_session(user_id, next_state, payload)
            self.telegram.send_message(chat_id, prompt)
            return
        if state != "contact":
            self.storage.clear_session(user_id)
            self.telegram.send_message(chat_id, "Состояние заявки сброшено. Начните заново.", main_menu())
            return
        contact_value = (contact or {}).get("phone_number") or text
        if not contact_value:
            self.telegram.send_message(chat_id, "Укажите способ связи текстом.")
            return
        lead = Lead(
            telegram_user_id=user_id,
            telegram_username=user.get("username", ""),
            company=payload["company"],
            name=payload["name"],
            role=payload["role"],
            need=payload["need"],
            contact=contact_value,
            source=payload.get("source", "direct"),
        )
        lead_id = self.storage.save_lead(lead)
        self.storage.clear_session(user_id)
        self.telegram.send_message(
            chat_id,
            f"✅ Заявка №{lead_id} принята. Мы изучим сценарий и свяжемся с вами.\n\n"
            "Отправляя заявку, вы согласились на использование указанных контактных данных для связи.",
            main_menu(),
        )
        self._notify_admin(lead_id, lead)

    def _notify_admin(self, lead_id: int, lead: Lead) -> None:
        if self.settings.admin_chat_id is None:
            return
        self.telegram.send_message(
            self.settings.admin_chat_id,
            self._lead_card_text(lead_id, lead, "new"),
            self._lead_card_keyboard(lead_id, "new"),
        )

    def _lead_card_text(self, lead_id: int, lead: Lead | Any, status: str) -> str:
        username = (
            f"@{html.escape(lead.telegram_username)}"
            if lead.telegram_username
            else "не указан"
        )
        status_line = f"{STATUS_ICONS.get(status, '•')} <b>{STATUS_LABELS.get(status, status)}</b>"
        return (
            f"<b>PU WORKSPACE · ЗАЯВКА #{lead_id}</b>\n"
            f"{status_line}\n\n"
            f"🏢 <b>{html.escape(lead.company)}</b>\n"
            f"👤 {html.escape(lead.name)} · {html.escape(lead.role)}\n"
            f"💬 <b>Что нужно решить</b>\n{html.escape(lead.need)}\n\n"
            f"☎️ <b>Контакт</b>: {html.escape(lead.contact)}\n"
            f"✈️ Telegram: {username}\n"
            f"📍 Источник: <code>{html.escape(lead.source)}</code>"
        )

    def _lead_card_keyboard(self, lead_id: int, status: str) -> dict[str, Any]:
        rows: list[list[dict[str, str]]] = []
        if status != "contacted":
            rows.append([{"text": "📞 Связались", "callback_data": f"lead:{lead_id}:contacted"}])
        if status != "pilot":
            rows.append([{"text": "🚀 Перевести в пилот", "callback_data": f"lead:{lead_id}:pilot"}])
        if status not in {"closed", "rejected"}:
            rows.append(
                [
                    {"text": "✅ Закрыть", "callback_data": f"lead:{lead_id}:closed"},
                    {"text": "⛔ Отказ", "callback_data": f"lead:{lead_id}:rejected"},
                ]
            )
        return {"inline_keyboard": rows}

    def _is_admin(self, user_id: int) -> bool:
        return self.settings.admin_chat_id is not None and user_id == self.settings.admin_chat_id

    def _handle_admin_command(self, chat_id: int, user_id: int, text: str) -> None:
        if not self._is_admin(user_id):
            self.telegram.send_message(chat_id, "Команда доступна только владельцу бота.", main_menu())
            return
        if text == "/stats":
            stats = self.storage.lead_stats()
            sources = self.storage.source_stats()
            source_lines = "\n".join(
                f"• {html.escape(source)}: {total}" for source, total in sources.items()
            ) or "• данных пока нет"
            self.telegram.send_message(
                chat_id,
                "<b>Статистика заявок</b>\n"
                f"Всего: {stats['total']}\nНовых: {stats['new']}\nСвязались: {stats['contacted']}\n"
                f"Пилот: {stats['pilot']}\nЗакрыто: {stats['closed']}\nОтказ: {stats['rejected']}\n\n"
                f"<b>Источники</b>\n{source_lines}",
            )
            return
        leads = self.storage.list_leads(10)
        if not leads:
            self.telegram.send_message(chat_id, "Заявок пока нет.")
            return
        lines = ["<b>Последние заявки</b>"]
        for lead in leads:
            lines.append(
                f"\n#{lead.id} · {STATUS_LABELS.get(lead.status, lead.status)}\n"
                f"{html.escape(lead.company)} — {html.escape(lead.name)}\n"
                f"{html.escape(lead.contact)} · источник: {html.escape(lead.source)}"
            )
        self.telegram.send_message(chat_id, "\n".join(lines))

    def _handle_lead_status_callback(
        self,
        chat_id: int,
        user: dict[str, Any],
        action: str,
        message: dict[str, Any],
    ) -> None:
        user_id = int(user["id"])
        if not self._is_admin(user_id):
            self.telegram.send_message(chat_id, "Управление заявками доступно только владельцу.")
            return
        try:
            _, lead_id_raw, status = action.split(":", 2)
            lead_id = int(lead_id_raw)
            current = self.storage.get_lead(lead_id)
            if current is not None and current.status == status:
                return
            changed = self.storage.set_lead_status(lead_id, status)
        except (ValueError, TypeError):
            self.telegram.send_message(chat_id, "Некорректная команда изменения статуса.")
            return
        if not changed:
            self.telegram.send_message(chat_id, f"Заявка #{lead_id} не найдена.")
            return
        lead = self.storage.get_lead(lead_id)
        if lead is None:
            return
        message_id = message.get("message_id")
        if message_id is not None:
            self.telegram.edit_message_text(
                chat_id,
                int(message_id),
                self._lead_card_text(lead_id, lead, status),
                self._lead_card_keyboard(lead_id, status),
            )
            return
        self.telegram.send_message(
            chat_id,
            self._lead_card_text(lead_id, lead, status),
            self._lead_card_keyboard(lead_id, status),
        )

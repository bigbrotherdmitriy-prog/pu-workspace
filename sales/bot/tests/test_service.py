import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.knowledge import ApprovedKnowledgeProvider
from app.service import SalesBotService, campaign_entry, campaign_menu
from app.storage import Lead, Storage
from app.telegram import TelegramError


class FakeTelegram:
    def __init__(self, fail: bool = False, fail_photo: bool = False) -> None:
        self.fail = fail
        self.fail_photo = fail_photo
        self.messages = []
        self.edits = []

    def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise RuntimeError("temporary failure")
        self.messages.append((chat_id, text, reply_markup))

    def send_photo(self, chat_id, photo, caption, reply_markup=None):
        if self.fail:
            raise RuntimeError("temporary failure")
        if self.fail_photo:
            raise TelegramError("Telegram rejected remote photo")
        self.messages.append((chat_id, caption, reply_markup, photo))

    def answer_callback(self, callback_query_id):
        return None

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append((chat_id, message_id, text, reply_markup))


class SalesBotServiceTest(unittest.TestCase):
    def settings(self, database_path: str) -> Settings:
        return Settings(
            token="test",
            admin_chat_id=None,
            database_path=database_path,
            website_url="https://puworkspace.ru",
            channel_url="https://t.me/puworkspace",
            presentation_url="",
            brochure_url="",
            poll_timeout=30,
        )

    def test_start_opens_main_menu_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram()
            storage = Storage(path)
            service = SalesBotService(self.settings(path), storage, telegram, ApprovedKnowledgeProvider())
            update = {
                "update_id": 1,
                "message": {"chat": {"id": 20}, "from": {"id": 10}, "text": "/start"},
            }
            service.handle_update(update)
            service.handle_update(update)
            self.assertEqual(len(telegram.messages), 1)
            self.assertTrue(storage.is_update_processed(1))

    def test_failed_send_does_not_lose_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram(fail=True)
            storage = Storage(path)
            service = SalesBotService(self.settings(path), storage, telegram, ApprovedKnowledgeProvider())
            update = {
                "update_id": 2,
                "message": {"chat": {"id": 20}, "from": {"id": 10}, "text": "/start"},
            }
            with self.assertRaises(RuntimeError):
                service.handle_update(update)
            self.assertFalse(storage.is_update_processed(2))

    def test_start_falls_back_to_text_when_remote_photo_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram(fail_photo=True)
            storage = Storage(path)
            service = SalesBotService(self.settings(path), storage, telegram, ApprovedKnowledgeProvider())
            service.handle_update({
                "update_id": 3,
                "message": {"chat": {"id": 20}, "from": {"id": 10}, "text": "/start"},
            })
            self.assertTrue(storage.is_update_processed(3))
            self.assertEqual(len(telegram.messages), 1)
            self.assertIn("PU Workspace", telegram.messages[0][1])

    def test_start_parameter_is_preserved_in_completed_lead(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram()
            storage = Storage(path)
            service = SalesBotService(self.settings(path), storage, telegram, ApprovedKnowledgeProvider())
            service.handle_update({"update_id": 10, "message": {"chat": {"id": 20}, "from": {"id": 10}, "text": "/start site_hero"}})
            service.handle_update({"update_id": 11, "callback_query": {"id": "c1", "from": {"id": 10}, "message": {"chat": {"id": 20}}, "data": "early_access"}})
            service.handle_update({"update_id": 12, "callback_query": {"id": "c2", "from": {"id": 10}, "message": {"chat": {"id": 20}}, "data": "lead_consent"}})
            for update_id, answer in enumerate(("Компания", "Иван", "Директор", "Контроль", "hello@example.com"), 13):
                service.handle_update({"update_id": update_id, "message": {"chat": {"id": 20}, "from": {"id": 10, "username": "client"}, "text": answer}})
            self.assertEqual(storage.get_lead(1).source, "site_hero")

    def test_ad_sources_open_relevant_campaign_entry(self):
        expectations = {
            "ad_obligations": "договорных обязательств",
            "ad_finance_chain": "ГПР, счетов и ДДС",
            "ad_email_control": "проектной переписки",
            "ad_self_hosted": "Самостоятельная версия",
            "ad_construction": "строительного проекта",
            "package_diagnostic": "Диагностика",
            "package_license": "Самостоятельная версия",
        }
        for source, expected in expectations.items():
            with self.subTest(source=source):
                entry = campaign_entry(source)
                self.assertIsNotNone(entry)
                self.assertIn(expected, entry.title)
                self.assertEqual(
                    campaign_menu(entry)["inline_keyboard"][0][0]["callback_data"],
                    "early_access",
                )

    def test_campaign_start_personalizes_first_message_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram()
            storage = Storage(path)
            service = SalesBotService(self.settings(path), storage, telegram, ApprovedKnowledgeProvider())
            service.handle_update({
                "update_id": 20,
                "message": {"chat": {"id": 20}, "from": {"id": 10}, "text": "/start ad_finance_chain"},
            })
            self.assertIn("ГПР, счетов и ДДС", telegram.messages[0][1])
            self.assertEqual(storage.get_session(10)[1]["source"], "ad_finance_chain")
            self.assertEqual(
                telegram.messages[0][2]["inline_keyboard"][0][0]["callback_data"],
                "early_access",
            )

    def test_development_has_its_own_lead_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram()
            storage = Storage(path)
            service = SalesBotService(self.settings(path), storage, telegram, ApprovedKnowledgeProvider())
            service.handle_update({"update_id": 30, "message": {"chat": {"id": 20}, "from": {"id": 10}, "text": "/start development_home"}})
            service.handle_update({"update_id": 31, "callback_query": {"id": "d1", "from": {"id": 10}, "message": {"chat": {"id": 20}}, "data": "development"}})
            service.handle_update({"update_id": 32, "callback_query": {"id": "d2", "from": {"id": 10}, "message": {"chat": {"id": 20}}, "data": "development_lead"}})
            service.handle_update({"update_id": 33, "callback_query": {"id": "d3", "from": {"id": 10}, "message": {"chat": {"id": 20}}, "data": "lead_consent"}})
            self.assertEqual(storage.get_session(10)[1]["source"], "bot_development")
            self.assertIn("компания или проект", telegram.messages[-1][1])

    def test_repeated_lead_status_callback_does_not_duplicate_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram()
            storage = Storage(path)
            lead_id = storage.save_lead(
                Lead(10, "client", "Компания", "Иван", "Директор", "Контроль", "contact")
            )
            settings = self.settings(path)
            settings = Settings(
                token=settings.token,
                admin_chat_id=10,
                database_path=settings.database_path,
                website_url=settings.website_url,
                channel_url=settings.channel_url,
                presentation_url=settings.presentation_url,
                brochure_url=settings.brochure_url,
                poll_timeout=settings.poll_timeout,
            )
            service = SalesBotService(settings, storage, telegram, ApprovedKnowledgeProvider())
            callback = {
                "from": {"id": 10},
                "message": {"chat": {"id": 10}},
                "data": f"lead:{lead_id}:contacted",
            }
            service.handle_update({"update_id": 100, "callback_query": {"id": "x1", **callback}})
            service.handle_update({"update_id": 101, "callback_query": {"id": "x2", **callback}})
            self.assertEqual(storage.get_lead(lead_id).status, "contacted")
            self.assertEqual(len(telegram.messages), 1)

    def test_status_callback_updates_existing_lead_card(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            telegram = FakeTelegram()
            storage = Storage(path)
            lead_id = storage.save_lead(
                Lead(10, "client", "Компания", "Иван", "Директор", "Контроль", "contact")
            )
            base = self.settings(path)
            settings = Settings(**{**base.__dict__, "admin_chat_id": 10})
            service = SalesBotService(settings, storage, telegram, ApprovedKnowledgeProvider())
            service.handle_update({
                "update_id": 200,
                "callback_query": {
                    "id": "e1",
                    "from": {"id": 10},
                    "message": {"chat": {"id": 10}, "message_id": 77},
                    "data": f"lead:{lead_id}:pilot",
                },
            })
            self.assertEqual(len(telegram.edits), 1)
            self.assertEqual(telegram.edits[0][1], 77)
            self.assertIn("PU WORKSPACE · ЗАЯВКА", telegram.edits[0][2])
            self.assertIn("Пилот", telegram.edits[0][2])


if __name__ == "__main__":
    unittest.main()

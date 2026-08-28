import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.knowledge import ApprovedKnowledgeProvider
from app.service import SalesBotService
from app.storage import Storage
from app.telegram import TelegramError


class FakeTelegram:
    def __init__(self, fail: bool = False, fail_photo: bool = False) -> None:
        self.fail = fail
        self.fail_photo = fail_photo
        self.messages = []

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


if __name__ == "__main__":
    unittest.main()

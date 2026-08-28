import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.storage import Lead, Storage


class StorageTest(unittest.TestCase):
    def test_session_and_update_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(str(Path(directory) / "test.sqlite3"))
            storage.set_session(7, "company", {"x": "y"})
            self.assertEqual(storage.get_session(7), ("company", {"x": "y"}))
            self.assertFalse(storage.is_update_processed(10))
            storage.mark_update_processed(10)
            self.assertTrue(storage.is_update_processed(10))
            self.assertEqual(storage.get_update_offset(), 11)
            storage.set_update_offset(25)
            self.assertEqual(storage.get_update_offset(), 25)

    def test_update_offset_survives_storage_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite3")
            Storage(path).set_update_offset(123)
            self.assertEqual(Storage(path).get_update_offset(), 123)

    def test_lead_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(str(Path(directory) / "test.sqlite3"))
            lead_id = storage.save_lead(
                Lead(1, "client", "Компания", "Иван", "Директор", "Контроль сроков", "+70000000000")
            )
            self.assertEqual(lead_id, 1)
            self.assertEqual(storage.get_lead(1).status, "new")
            self.assertTrue(storage.set_lead_status(1, "pilot"))
            self.assertEqual(storage.get_lead(1).status, "pilot")
            self.assertEqual(storage.lead_stats()["pilot"], 1)
            self.assertEqual(storage.list_leads()[0].company, "Компания")
            self.assertEqual(storage.get_lead(1).source, "direct")
            self.assertEqual(storage.source_stats(), {"direct": 1})

    def test_existing_database_is_migrated_without_losing_leads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.sqlite3")
            db = sqlite3.connect(path)
            try:
                db.execute(
                    """CREATE TABLE leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL, telegram_username TEXT NOT NULL,
                    company TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
                    need TEXT NOT NULL, contact TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL)"""
                )
                db.execute(
                    "INSERT INTO leads(telegram_user_id, telegram_username, company, name, role, need, contact, created_at) VALUES (1, '', 'Старая', 'Иван', 'Роль', 'Задача', 'Контакт', '2026-01-01')"
                )
                db.commit()
            finally:
                db.close()
            storage = Storage(path)
            self.assertEqual(storage.get_lead(1).company, "Старая")
            self.assertEqual(storage.get_lead(1).source, "direct")


if __name__ == "__main__":
    unittest.main()

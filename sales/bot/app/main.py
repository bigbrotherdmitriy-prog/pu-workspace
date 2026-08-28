from __future__ import annotations

import logging
import random
import time

from app.config import Settings
from app.knowledge import ApprovedKnowledgeProvider
from app.service import SalesBotService
from app.storage import Storage
from app.telegram import TelegramClient, TelegramError


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    telegram = TelegramClient(settings.token)
    storage = Storage(settings.database_path)
    service = SalesBotService(
        settings=settings,
        storage=storage,
        telegram=telegram,
        answers=ApprovedKnowledgeProvider(),
    )
    telegram.call("deleteWebhook", {"drop_pending_updates": False})
    offset = storage.get_update_offset()
    failures = 0
    logging.info("PU Workspace sales bot started")
    while True:
        try:
            for update in telegram.get_updates(offset, settings.poll_timeout):
                try:
                    service.handle_update(update)
                    offset = max(offset, int(update["update_id"]) + 1)
                    storage.set_update_offset(offset)
                except Exception:
                    logging.exception("Failed to process update %s", update.get("update_id"))
            failures = 0
        except TelegramError:
            logging.exception("Telegram polling failed")
            failures += 1
            delay = min(60.0, 2.0 ** min(failures, 5)) + random.uniform(0, 1)
            logging.warning("Retrying Telegram polling in %.1f seconds", delay)
            time.sleep(delay)


if __name__ == "__main__":
    run()

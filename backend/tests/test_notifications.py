import os
import unittest
from unittest.mock import patch

from app.core.notifications import notify_telegram, telegram_configured


class NotificationTests(unittest.TestCase):
    def test_disabled_without_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(telegram_configured())
            self.assertFalse(notify_telegram("test"))


if __name__ == "__main__":
    unittest.main()

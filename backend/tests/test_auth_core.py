import unittest

from datetime import datetime, timedelta, timezone

from app.core.auth import (
    LOGIN_FAILURE_LIMIT,
    clear_login_failures,
    hash_password,
    login_is_throttled,
    record_login_failure,
    verify_password,
)


class PasswordTests(unittest.TestCase):
    def test_scrypt_round_trip(self):
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(encoded.startswith("scrypt:v1:"))
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password value", encoded))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("too-short")

    def test_login_throttle_expires_and_can_be_cleared(self):
        key = "test-client-and-account"
        clear_login_failures(key)
        now = datetime(2026, 8, 29, tzinfo=timezone.utc)
        for _ in range(LOGIN_FAILURE_LIMIT):
            record_login_failure(key, now)
        self.assertTrue(login_is_throttled(key, now))
        self.assertFalse(login_is_throttled(key, now + timedelta(minutes=16)))
        record_login_failure(key, now + timedelta(minutes=16))
        clear_login_failures(key)
        self.assertFalse(login_is_throttled(key, now + timedelta(minutes=16)))


if __name__ == "__main__":
    unittest.main()

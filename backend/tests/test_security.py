import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.api.google_drive import _make_oauth_state, _project_from_oauth_state
from app.core.token_crypto import (
    PREFIX,
    TokenEncryptionError,
    decrypt_token,
    encrypt_token,
    rotate_token,
)
from app.core.oauth_state import make_oauth_state, project_from_oauth_state
from fastapi import HTTPException


class SecurityTests(unittest.TestCase):
    def test_token_round_trip_is_encrypted(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": key}):
            stored = encrypt_token("secret-token")
            self.assertTrue(stored.startswith(PREFIX))
            self.assertNotIn("secret-token", stored)
            self.assertEqual(decrypt_token(stored), "secret-token")

    def test_invalid_token_key_fails_closed(self):
        with patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": "bad"}):
            with self.assertRaises(TokenEncryptionError):
                encrypt_token("secret")

    def test_previous_key_can_decrypt_and_rotate_stored_token(self):
        previous_key = Fernet.generate_key().decode()
        active_key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": previous_key}, clear=False):
            stored = encrypt_token("rotating-secret")
        with patch.dict(
            os.environ,
            {
                "TOKEN_ENCRYPTION_KEY": active_key,
                "TOKEN_ENCRYPTION_PREVIOUS_KEYS": previous_key,
            },
            clear=False,
        ):
            self.assertEqual(decrypt_token(stored), "rotating-secret")
            rotated = rotate_token(stored)
        with patch.dict(
            os.environ,
            {"TOKEN_ENCRYPTION_KEY": active_key, "TOKEN_ENCRYPTION_PREVIOUS_KEYS": ""},
            clear=False,
        ):
            self.assertEqual(decrypt_token(rotated), "rotating-secret")
        self.assertNotEqual(rotated, stored)

    def test_unknown_key_fails_closed_even_when_previous_keys_are_configured(self):
        unknown_key = Fernet.generate_key().decode()
        active_key = Fernet.generate_key().decode()
        previous_key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"TOKEN_ENCRYPTION_KEY": unknown_key}, clear=False):
            stored = encrypt_token("secret")
        with patch.dict(
            os.environ,
            {
                "TOKEN_ENCRYPTION_KEY": active_key,
                "TOKEN_ENCRYPTION_PREVIOUS_KEYS": previous_key,
            },
            clear=False,
        ):
            with self.assertRaises(TokenEncryptionError):
                decrypt_token(stored)

    def test_oauth_state_round_trip_and_tamper_rejection(self):
        with patch.dict(os.environ, {"APP_SECRET_KEY": "a" * 32}):
            state = _make_oauth_state(42)
            self.assertEqual(_project_from_oauth_state(state), 42)
            tampered = ("A" if state[0] != "A" else "B") + state[1:]
            with self.assertRaises(HTTPException):
                _project_from_oauth_state(tampered)

    def test_oauth_state_cannot_be_replayed_between_providers(self):
        with patch.dict(os.environ, {"APP_SECRET_KEY": "a" * 32}):
            state = make_oauth_state(42, "yandex_disk")
            self.assertEqual(project_from_oauth_state(state, "yandex_disk"), 42)
            with self.assertRaises(HTTPException):
                project_from_oauth_state(state, "google")


if __name__ == "__main__":
    unittest.main()

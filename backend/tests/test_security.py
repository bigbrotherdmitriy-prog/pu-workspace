import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.api.google_drive import _make_oauth_state, _project_from_oauth_state
from app.core.token_crypto import PREFIX, TokenEncryptionError, decrypt_token, encrypt_token
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

    def test_oauth_state_round_trip_and_tamper_rejection(self):
        with patch.dict(os.environ, {"APP_SECRET_KEY": "a" * 32}):
            state = _make_oauth_state(42)
            self.assertEqual(_project_from_oauth_state(state), 42)
            tampered = ("A" if state[0] != "A" else "B") + state[1:]
            with self.assertRaises(HTTPException):
                _project_from_oauth_state(tampered)


if __name__ == "__main__":
    unittest.main()

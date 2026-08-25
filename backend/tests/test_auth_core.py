import unittest

from app.core.auth import hash_password, verify_password


class PasswordTests(unittest.TestCase):
    def test_scrypt_round_trip(self):
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(encoded.startswith("scrypt:v1:"))
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong password value", encoded))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("too-short")


if __name__ == "__main__":
    unittest.main()

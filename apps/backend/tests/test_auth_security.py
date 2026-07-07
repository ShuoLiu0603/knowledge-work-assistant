from __future__ import annotations

import unittest

from fastapi import HTTPException

from app.core.security import decode_access_token, hash_password, verify_password
from app.schemas.auth import UserCreate
from app.services.auth_service import authenticate_user, refresh_tokens, register_user, revoke_refresh_token
from helpers import isolated_session


class AuthSecurityTests(unittest.TestCase):
    def test_register_login_refresh_and_revoke_flow(self) -> None:
        with isolated_session() as session:
            auth = register_user(
                session,
                UserCreate(
                    email=" Demo.User@Example.COM ",
                    username="Demo",
                    password="Password123!",
                ),
            )

            self.assertEqual(auth.user.email, "demo.user@example.com")
            self.assertTrue(auth.user.is_admin)
            self.assertEqual(auth.user.security_level, 5)
            self.assertEqual(decode_access_token(auth.access_token), auth.user.id)

            user = authenticate_user(session, "demo.user@example.com", "Password123!")
            self.assertTrue(user.hashed_password.startswith("$2b$"))
            self.assertNotIn("Password123!", user.hashed_password)
            self.assertTrue(verify_password("Password123!", user.hashed_password))

            with self.assertRaises(HTTPException) as invalid_login:
                authenticate_user(session, "demo.user@example.com", "wrong-password")
            self.assertEqual(invalid_login.exception.status_code, 401)

            refreshed = refresh_tokens(session, auth.refresh_token)
            self.assertEqual(refreshed.user.id, auth.user.id)
            self.assertNotEqual(refreshed.refresh_token, auth.refresh_token)

            with self.assertRaises(HTTPException):
                refresh_tokens(session, auth.refresh_token)

            revoke_refresh_token(session, refreshed.refresh_token)
            with self.assertRaises(HTTPException):
                refresh_tokens(session, refreshed.refresh_token)

    def test_only_first_registered_user_is_bootstrap_admin(self) -> None:
        with isolated_session() as session:
            first = register_user(
                session,
                UserCreate(email="first@example.com", username="First", password="Password123!"),
            )
            second = register_user(
                session,
                UserCreate(email="second@example.com", username="Second", password="Password123!"),
            )

            self.assertTrue(first.user.is_admin)
            self.assertEqual(first.user.security_level, 5)
            self.assertFalse(second.user.is_admin)
            self.assertEqual(second.user.security_level, 1)

    def test_password_hashes_are_salted_and_legacy_pbkdf2_is_supported(self) -> None:
        first_hash = hash_password("Password123!")
        second_hash = hash_password("Password123!")

        self.assertTrue(first_hash.startswith("$2b$"))
        self.assertTrue(second_hash.startswith("$2b$"))
        self.assertNotEqual(first_hash, second_hash)
        self.assertTrue(verify_password("Password123!", first_hash))
        self.assertFalse(verify_password("wrong-password", first_hash))

        legacy_hash = (
            "pbkdf2_sha256$260000$"
            "bGVnYWN5LXNhbHQtMTIzNA==$"
            "na5mO-OW5avJIVRLyA9I7SrqA5Sm7xhKClgOi-4Wbiw="
        )
        self.assertTrue(verify_password("Password123!", legacy_hash))


if __name__ == "__main__":
    unittest.main()

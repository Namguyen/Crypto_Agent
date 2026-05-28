import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class AuthFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["AUTH_DB_PATH"] = os.path.join(cls.tmp.name, "auth-test.db")
        os.environ["FLASK_SECRET_KEY"] = "test-flask-secret-key-0000000000000000"
        os.environ["JWT_SECRET_KEY"] = "test-access-secret-key-000000000000000"
        os.environ["REFRESH_TOKEN_SECRET"] = "test-refresh-secret-key-000000000000"
        os.environ["ACCESS_TOKEN_EXP_SECONDS"] = "900"
        os.environ["REFRESH_TOKEN_EXP_SECONDS"] = "604800"
        os.environ["AUTH_ALLOW_REGISTRATION"] = "true"
        os.environ["SELF_AUTH_USERNAME"] = "seed"
        os.environ["SELF_AUTH_PASSWORD"] = "password123"
        os.environ["DEEPSEEK_API_KEY"] = "test"

        import app as app_module

        cls.app = app_module.app

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_seed_user_login_refresh_logout(self):
        with TestClient(self.app) as client:
            login = client.post(
                "/api/auth/login",
                json={"login": "seed", "password": "password123"},
            )
            self.assertEqual(login.status_code, 200)
            access_token = login.json()["accessToken"]

            me = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self.assertTrue(me.json()["authenticated"])
            self.assertEqual(me.json()["user"]["username"], "seed")

            protected = client.post(
                "/api/chat",
                json={},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self.assertEqual(protected.status_code, 400)
            self.assertEqual(protected.json()["error"], "Empty message")

            refresh = client.post("/api/auth/refresh")
            self.assertEqual(refresh.status_code, 200)
            self.assertIn("accessToken", refresh.json())

            logout = client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self.assertEqual(logout.status_code, 200)

            refresh_after_logout = client.post("/api/auth/refresh")
            self.assertEqual(refresh_after_logout.status_code, 401)

    def test_register_creates_login_session(self):
        with TestClient(self.app) as client:
            res = client.post(
                "/api/auth/register",
                json={
                    "username": "newuser",
                    "email": "newuser@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(res.status_code, 201)
            data = res.json()
            self.assertIn("accessToken", data)
            self.assertEqual(data["user"]["username"], "newuser")

    def test_auth_pages_render_forms(self):
        with TestClient(self.app) as client:
            login = client.get("/login")
            self.assertEqual(login.status_code, 200)
            self.assertIn('id="authForm"', login.text)
            self.assertIn("/api/auth/login", login.text)

            register = client.get("/register")
            self.assertEqual(register.status_code, 200)
            self.assertIn('id="authForm"', register.text)
            self.assertIn("/api/auth/register", register.text)

    def test_notes_are_scoped_to_user(self):
        seed_client = TestClient(self.app)
        other_client = TestClient(self.app)

        seed_login = seed_client.post(
            "/api/auth/login",
            json={"login": "seed", "password": "password123"},
        )
        self.assertEqual(seed_login.status_code, 200)
        seed_token = seed_login.json()["accessToken"]

        seed_note = seed_client.post(
            "/api/notes",
            json={"content": "seed-only datapoint"},
            headers={"Authorization": f"Bearer {seed_token}"},
        )
        self.assertEqual(seed_note.status_code, 201)

        other_register = other_client.post(
            "/api/auth/register",
            json={
                "username": "notesuser",
                "email": "notesuser@example.com",
                "password": "password123",
            },
        )
        self.assertEqual(other_register.status_code, 201)
        other_token = other_register.json()["accessToken"]

        other_notes = other_client.get(
            "/api/notes",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(other_notes.status_code, 200)
        self.assertEqual(other_notes.json()["notes"], [])

        seed_notes = seed_client.get(
            "/api/notes",
            headers={"Authorization": f"Bearer {seed_token}"},
        )
        self.assertEqual(seed_notes.status_code, 200)
        self.assertEqual(seed_notes.json()["notes"][0]["content"], "seed-only datapoint")

    def test_admin_pages_and_apis(self):
        with TestClient(self.app) as client:
            login = client.post("/api/auth/login", json={"login": "seed", "password": "password123"})
            token = login.json()["accessToken"]

            page = client.get("/admin")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Database Users & Request Logs", page.text)

            users = client.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(users.status_code, 200)
            self.assertGreaterEqual(len(users.json()["users"]), 1)

            logs = client.get("/api/admin/request-logs", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(logs.status_code, 200)
            self.assertIn("logs", logs.json())

    def test_chat_modes_and_request_logging(self):
        with TestClient(self.app) as client, patch("backend.app.run_agent", return_value="reasoned reply") as run_agent:
            login = client.post("/api/auth/login", json={"login": "seed", "password": "password123"})
            token = login.json()["accessToken"]

            modes = client.get("/api/chat/modes")
            self.assertEqual(modes.status_code, 200)
            self.assertEqual(modes.json()["defaultMode"], "instant")
            self.assertEqual({mode["key"] for mode in modes.json()["modes"]}, {"instant", "reasoning"})

            invalid = client.post(
                "/api/chat",
                json={"message": "Compare BTC and ETH", "mode": "slow"},
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(invalid.json()["error"], "Invalid chat mode")

            res = client.post(
                "/api/chat",
                json={"message": "Compare BTC and ETH", "mode": "reasoning"},
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["mode"], "reasoning")
            self.assertEqual(res.json()["reply"], "reasoned reply")
            self.assertEqual(run_agent.call_args.kwargs["mode"], "reasoning")

            logs = client.get("/api/admin/request-logs", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(logs.status_code, 200)
            self.assertEqual(logs.json()["logs"][0]["mode"], "reasoning")
            self.assertTrue(logs.json()["logs"][0]["model"])

    def test_in_app_price_notifications(self):
        market_payload = {
            "bitcoin": {"usd": 73000, "usd_24h_change": -4.2},
            "ethereum": {"usd": 2100, "usd_24h_change": 1.1},
            "solana": {"usd": 160, "usd_24h_change": 0.5},
            "ripple": {"usd": 1.25, "usd_24h_change": 0.2},
            "binancecoin": {"usd": 620, "usd_24h_change": 0.8},
        }

        with TestClient(self.app) as client, patch("backend.app.fetch_market_prices", return_value=market_payload):
            login = client.post("/api/auth/login", json={"login": "seed", "password": "password123"})
            token = login.json()["accessToken"]
            headers = {"Authorization": f"Bearer {token}"}

            initial = client.get("/api/notifications", headers=headers)
            self.assertEqual(initial.status_code, 200)
            self.assertGreaterEqual(len(initial.json()["settings"]), 5)

            checked = client.post("/api/notifications/check", headers=headers)
            self.assertEqual(checked.status_code, 200)
            self.assertEqual(len(checked.json()["created"]), 1)
            self.assertEqual(checked.json()["created"][0]["symbol"], "BTC")
            self.assertEqual(checked.json()["unreadCount"], 1)

            read = client.post("/api/notifications/read", headers=headers)
            self.assertEqual(read.status_code, 200)
            self.assertEqual(read.json()["unreadCount"], 0)

    def test_missing_auth_blocks_chat(self):
        with TestClient(self.app) as client:
            res = client.post("/api/chat", json={})
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.json()["error"], "Login required")


if __name__ == "__main__":
    unittest.main()

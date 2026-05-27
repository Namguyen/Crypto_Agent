import os
import tempfile
import unittest


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
        cls.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_seed_user_login_refresh_logout(self):
        with self.app.test_client() as client:
            login = client.post(
                "/api/auth/login",
                json={"login": "seed", "password": "password123"},
            )
            self.assertEqual(login.status_code, 200)
            access_token = login.get_json()["accessToken"]

            me = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self.assertTrue(me.get_json()["authenticated"])
            self.assertEqual(me.get_json()["user"]["username"], "seed")

            protected = client.post(
                "/api/chat",
                json={},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self.assertEqual(protected.status_code, 400)
            self.assertEqual(protected.get_json()["error"], "Empty message")

            refresh = client.post("/api/auth/refresh")
            self.assertEqual(refresh.status_code, 200)
            self.assertIn("accessToken", refresh.get_json())

            logout = client.post(
                "/api/auth/logout",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self.assertEqual(logout.status_code, 200)

            refresh_after_logout = client.post("/api/auth/refresh")
            self.assertEqual(refresh_after_logout.status_code, 401)

    def test_register_creates_login_session(self):
        with self.app.test_client() as client:
            res = client.post(
                "/api/auth/register",
                json={
                    "username": "newuser",
                    "email": "newuser@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(res.status_code, 201)
            data = res.get_json()
            self.assertIn("accessToken", data)
            self.assertEqual(data["user"]["username"], "newuser")

    def test_missing_auth_blocks_chat(self):
        with self.app.test_client() as client:
            res = client.post("/api/chat", json={})
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.get_json()["error"], "Login required")


if __name__ == "__main__":
    unittest.main()

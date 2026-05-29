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

    def test_user_can_update_profile(self):
        with TestClient(self.app) as client:
            login = client.post(
                "/api/auth/login",
                json={"login": "seed", "password": "password123"},
            )
            token = login.json()["accessToken"]
            headers = {"Authorization": f"Bearer {token}"}

            updated = client.patch(
                "/api/users/me",
                json={
                    "displayName": "Seed Trader",
                    "bio": "Macro-aware BTC and ETH watcher.",
                    "picture": "https://example.com/avatar.png",
                },
                headers=headers,
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["user"]["displayName"], "Seed Trader")
            self.assertEqual(updated.json()["user"]["bio"], "Macro-aware BTC and ETH watcher.")

            me = client.get("/api/auth/me", headers=headers)
            self.assertEqual(me.json()["user"]["name"], "Seed Trader")

    def test_auth_pages_render_forms(self):
        with TestClient(self.app) as client:
            index = client.get("/")
            self.assertEqual(index.status_code, 200)
            self.assertIn('id="msgs"', index.text)
            self.assertIn("CRYPTO AGENT online", index.text)
            self.assertIn('id="accountMenu"', index.text)
            self.assertIn("handleAccountTrigger", index.text)
            self.assertIn('id="globalDirectChatBubble"', index.text)
            self.assertIn('id="globalDirectChatPanel"', index.text)
            self.assertIn('id="globalDirectChatInput"', index.text)
            self.assertNotIn('data-initial-page=', index.text)
            self.assertNotIn('<div id="friendSearchResults"', index.text)
            self.assertNotIn('<section class="panel chat" id="socialChatCol"', index.text)
            self.assertNotIn('<form class="form" id="profileForm"', index.text)

            friends = client.get("/friends")
            self.assertEqual(friends.status_code, 200)
            self.assertIn('id="friendSearchResults"', friends.text)
            self.assertIn('id="socialChatCol"', friends.text)
            self.assertIn('id="socialChatMessages"', friends.text)
            self.assertIn('id="directChatBubble"', friends.text)
            self.assertIn("startDirectChatBubbleDrag", friends.text)
            self.assertIn("API_FRIEND_REQUESTS", friends.text)
            self.assertIn("API_CONVERSATIONS", friends.text)
            self.assertIn("cryptoAgentDirectConversation", friends.text)

            profiles = client.get("/profiles")
            self.assertEqual(profiles.status_code, 200)
            self.assertIn('id="profileForm"', profiles.text)
            self.assertIn('id="globalDirectChatBubble"', profiles.text)
            self.assertIn('id="globalDirectChatPanel"', profiles.text)
            self.assertIn('id="profileDisplayName"', profiles.text)
            self.assertIn('id="profileBio"', profiles.text)
            self.assertIn('id="profilePicture"', profiles.text)
            self.assertIn("API_USER_ME", profiles.text)

            profile = client.get("/profile")
            self.assertEqual(profile.status_code, 200)
            self.assertIn('id="profileForm"', profile.text)

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

    def test_user_search_and_friend_requests(self):
        with TestClient(self.app) as client:
            seed_login = client.post("/api/auth/login", json={"login": "seed", "password": "password123"})
            seed_token = seed_login.json()["accessToken"]
            seed_headers = {"Authorization": f"Bearer {seed_token}"}
            seed_id = seed_login.json()["user"]["id"]

            bob_register = client.post(
                "/api/auth/register",
                json={
                    "username": "socialbob",
                    "email": "socialbob@example.com",
                    "password": "password123",
                },
            )
            bob_token = bob_register.json()["accessToken"]
            bob_headers = {"Authorization": f"Bearer {bob_token}"}
            bob_id = bob_register.json()["user"]["id"]

            charlie_register = client.post(
                "/api/auth/register",
                json={
                    "username": "socialcharlie",
                    "email": "socialcharlie@example.com",
                    "password": "password123",
                },
            )
            charlie_token = charlie_register.json()["accessToken"]
            charlie_headers = {"Authorization": f"Bearer {charlie_token}"}
            charlie_id = charlie_register.json()["user"]["id"]

            search = client.get("/api/users/search?username=social", headers=seed_headers)
            self.assertEqual(search.status_code, 200)
            found_usernames = {user["username"] for user in search.json()["users"]}
            self.assertIn("socialbob", found_usernames)
            self.assertIn("socialcharlie", found_usernames)
            self.assertNotIn("seed", found_usernames)

            self_request = client.post(
                "/api/friends/requests",
                json={"to": seed_id},
                headers=seed_headers,
            )
            self.assertEqual(self_request.status_code, 400)

            request = client.post(
                "/api/friends/requests",
                json={"to": bob_id, "message": "Let's compare market notes."},
                headers=seed_headers,
            )
            self.assertEqual(request.status_code, 201)
            request_id = request.json()["request"]["id"]
            self.assertEqual(request.json()["request"]["from"]["username"], "seed")
            self.assertEqual(request.json()["request"]["to"]["username"], "socialbob")

            duplicate = client.post(
                "/api/friends/requests",
                json={"to": bob_id},
                headers=seed_headers,
            )
            self.assertEqual(duplicate.status_code, 409)

            reverse_duplicate = client.post(
                "/api/friends/requests",
                json={"to": seed_id},
                headers=bob_headers,
            )
            self.assertEqual(reverse_duplicate.status_code, 409)

            bob_requests = client.get("/api/friends/requests", headers=bob_headers)
            self.assertEqual(bob_requests.status_code, 200)
            self.assertEqual(bob_requests.json()["received"][0]["id"], request_id)

            forbidden_accept = client.post(
                f"/api/friends/requests/{request_id}/accept",
                headers=charlie_headers,
            )
            self.assertEqual(forbidden_accept.status_code, 403)

            accepted = client.post(
                f"/api/friends/requests/{request_id}/accept",
                headers=bob_headers,
            )
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()["friend"]["username"], "seed")

            seed_friends = client.get("/api/friends", headers=seed_headers)
            bob_friends = client.get("/api/friends", headers=bob_headers)
            self.assertEqual(seed_friends.status_code, 200)
            self.assertEqual(bob_friends.status_code, 200)
            self.assertIn("socialbob", {friend["username"] for friend in seed_friends.json()["friends"]})
            self.assertIn("seed", {friend["username"] for friend in bob_friends.json()["friends"]})

            forbidden_conversation = client.post(
                "/api/conversations/direct",
                json={"friendId": charlie_id},
                headers=seed_headers,
            )
            self.assertEqual(forbidden_conversation.status_code, 403)

            direct_conversation = client.post(
                "/api/conversations/direct",
                json={"friendId": bob_id},
                headers=seed_headers,
            )
            self.assertEqual(direct_conversation.status_code, 200)
            conversation_id = direct_conversation.json()["conversation"]["id"]
            self.assertEqual(direct_conversation.json()["conversation"]["otherUser"]["username"], "socialbob")

            same_conversation = client.post(
                "/api/conversations/direct",
                json={"friendId": seed_id},
                headers=bob_headers,
            )
            self.assertEqual(same_conversation.status_code, 200)
            self.assertEqual(same_conversation.json()["conversation"]["id"], conversation_id)

            sent_message = client.post(
                f"/api/conversations/{conversation_id}/messages",
                json={"content": "BTC moved fast today."},
                headers=seed_headers,
            )
            self.assertEqual(sent_message.status_code, 201)
            self.assertEqual(sent_message.json()["message"]["content"], "BTC moved fast today.")
            self.assertTrue(sent_message.json()["message"]["isOwn"])

            bob_messages = client.get(
                f"/api/conversations/{conversation_id}/messages",
                headers=bob_headers,
            )
            self.assertEqual(bob_messages.status_code, 200)
            self.assertEqual(bob_messages.json()["messages"][0]["content"], "BTC moved fast today.")
            self.assertFalse(bob_messages.json()["messages"][0]["isOwn"])

            bob_conversations = client.get("/api/conversations", headers=bob_headers)
            self.assertEqual(bob_conversations.status_code, 200)
            self.assertEqual(bob_conversations.json()["conversations"][0]["unreadCount"], 1)

            marked_read = client.post(
                f"/api/conversations/{conversation_id}/read",
                headers=bob_headers,
            )
            self.assertEqual(marked_read.status_code, 200)
            bob_conversations_after_read = client.get("/api/conversations", headers=bob_headers)
            self.assertEqual(bob_conversations_after_read.json()["conversations"][0]["unreadCount"], 0)

            charlie_request = client.post(
                "/api/friends/requests",
                json={"to": charlie_id, "message": "Test decline flow."},
                headers=seed_headers,
            )
            self.assertEqual(charlie_request.status_code, 201)
            charlie_request_id = charlie_request.json()["request"]["id"]

            declined = client.post(
                f"/api/friends/requests/{charlie_request_id}/decline",
                headers=charlie_headers,
            )
            self.assertEqual(declined.status_code, 200)

            charlie_requests = client.get("/api/friends/requests", headers=charlie_headers)
            self.assertEqual(charlie_requests.json()["received"], [])

    def test_missing_auth_blocks_chat(self):
        with TestClient(self.app) as client:
            res = client.post("/api/chat", json={})
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.json()["error"], "Login required")


if __name__ == "__main__":
    unittest.main()

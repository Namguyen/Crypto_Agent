import os
import tempfile
import unittest
from unittest.mock import patch

import requests
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
        os.environ["SELF_AUTH_IS_ADMIN"] = "true"
        os.environ["DEEPSEEK_API_KEY"] = "test"
        os.environ["UPLOAD_ROOT"] = os.path.join(cls.tmp.name, "uploads")
        os.environ["NOTE_EMBEDDING_BACKEND"] = "hash"

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
                    "aiProfile": {
                        "experienceLevel": "advanced",
                        "communicationStyle": "executive",
                        "riskProfile": "balanced",
                        "preferredDepth": "short",
                        "favoriteAssets": "BTC, ETH",
                        "goals": "Macro-aware long-term accumulation.",
                    },
                },
                headers=headers,
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["user"]["displayName"], "Seed Trader")
            self.assertEqual(updated.json()["user"]["bio"], "Macro-aware BTC and ETH watcher.")
            ai_profile = updated.json()["user"]["aiProfile"]
            self.assertEqual(ai_profile["experienceLevel"], "advanced")
            self.assertEqual(ai_profile["communicationStyle"], "executive")
            self.assertEqual(ai_profile["riskProfile"], "balanced")
            self.assertEqual(ai_profile["preferredDepth"], "short")
            self.assertEqual(ai_profile["favoriteAssets"], "BTC, ETH")
            self.assertEqual(ai_profile["goals"], "Macro-aware long-term accumulation.")

            png_bytes = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
                b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
                b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            upload = client.post(
                "/api/users/me/picture",
                content=png_bytes,
                headers={**headers, "Content-Type": "image/png"},
            )
            self.assertEqual(upload.status_code, 200)
            picture = upload.json()["picture"]
            self.assertTrue(picture.startswith("/uploads/profile_pictures/"))
            self.assertEqual(upload.json()["user"]["picture"], picture)

            image_response = client.get(picture)
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(image_response.content, png_bytes)

            updated_with_uploaded_picture = client.patch(
                "/api/users/me",
                json={
                    "displayName": "Seed Trader",
                    "bio": "Macro-aware BTC and ETH watcher.",
                    "picture": picture,
                },
                headers=headers,
            )
            self.assertEqual(updated_with_uploaded_picture.status_code, 200)
            self.assertEqual(updated_with_uploaded_picture.json()["user"]["picture"], picture)

            me = client.get("/api/auth/me", headers=headers)
            self.assertEqual(me.json()["user"]["name"], "Seed Trader")
            self.assertEqual(me.json()["user"]["aiProfile"]["communicationStyle"], "executive")
            self.assertEqual(me.json()["user"]["aiProfile"]["favoriteAssets"], "BTC, ETH")

    def test_auth_pages_render_forms(self):
        with TestClient(self.app) as client:
            index = client.get("/")
            self.assertEqual(index.status_code, 200)
            self.assertIn('id="msgs"', index.text)
            self.assertNotIn("Online. Ask me about", index.text)
            self.assertNotIn('class="brand-mark">Coin', index.text)
            self.assertIn('id="mainTabAgent"', index.text)
            self.assertIn('id="mainTabPortfolio"', index.text)
            self.assertIn('id="mainTabMessages"', index.text)
            self.assertIn('id="mainTabFriends"', index.text)
            self.assertIn('id="mainTabNotes"', index.text)
            self.assertIn('id="themeToggle"', index.text)
            self.assertIn('id="agentView"', index.text)
            self.assertIn("setMainTab", index.text)
            self.assertIn('id="chatModePicker"', index.text)
            self.assertIn('id="modeMenu"', index.text)
            self.assertIn("setChatModeFromMenu", index.text)
            self.assertIn("hideRecommendationChipsForSession", index.text)
            self.assertIn("recommendationChipsDismissed", index.text)
            self.assertIn('id="accountMenu"', index.text)
            self.assertIn("handleAccountTrigger", index.text)
            self.assertIn('id="messagesCol"', index.text)
            self.assertIn('id="messagesUnreadCount"', index.text)
            self.assertIn('id="friendsView"', index.text)
            self.assertIn('id="friendSearchResults"', index.text)
            self.assertIn('id="portfolioView"', index.text)
            self.assertIn('id="portfolioPrompt"', index.text)
            self.assertIn('id="portfolioDraftTable"', index.text)
            self.assertIn('id="portfolioHoldings"', index.text)
            self.assertIn('id="portfolioChart"', index.text)
            self.assertIn('id="portfolioRefreshBtn"', index.text)
            self.assertIn("API_PORTFOLIO", index.text)
            self.assertIn("loadPortfolio", index.text)
            self.assertIn("parsePortfolioPrompt", index.text)
            self.assertIn("refreshPortfolio", index.text)
            self.assertIn("MESSAGE_TAB_POLL_MS", index.text)
            self.assertIn("NOTIFICATION_POLL_MS", index.text)
            self.assertIn("messageTabUnreadTotal", index.text)
            self.assertIn("connectMessageTabSocket", index.text)
            self.assertIn("messageTabSocket", index.text)
            self.assertIn("normalizeMessageTabMessage", index.text)
            self.assertIn("applyMessageBackgroundNotifications", index.text)
            self.assertIn("API_PRICE_DATA", index.text)
            self.assertIn("API_AGENT_GREETING", index.text)
            self.assertIn("loadAgentGreeting", index.text)
            self.assertIn("DEFAULT_MARKET_SYMBOLS", index.text)
            self.assertIn("API_RECOMMENDATIONS", index.text)
            self.assertIn("loadRecommendations", index.text)
            self.assertIn("CHECK NOW", index.text)
            self.assertIn("/api/dev/reload-version", index.text)
            self.assertNotIn('data-initial-page=', index.text)
            self.assertNotIn('<section class="panel chat" id="socialChatCol"', index.text)
            self.assertNotIn('<form class="form" id="profileForm"', index.text)
            self.assertNotIn('id="globalDirectChatDock"', index.text)
            self.assertNotIn('id="globalDirectChatPanels"', index.text)
            self.assertNotIn("global-chat-panel", index.text)
            self.assertNotIn("<span>Instant mode</span>", index.text)
            self.assertNotIn("<span>Reasoning mode</span>", index.text)

            friends = client.get("/friends")
            self.assertEqual(friends.status_code, 200)
            self.assertIn('id="friendSearchResults"', friends.text)
            self.assertIn('id="socialChatCol"', friends.text)
            self.assertIn('id="socialChatMessages"', friends.text)
            self.assertIn('id="directChatBubble"', friends.text)
            self.assertIn("direct-chat-bubble-close", friends.text)
            self.assertIn("startDirectChatBubbleDrag", friends.text)
            self.assertIn("API_FRIEND_REQUESTS", friends.text)
            self.assertIn("API_CONVERSATIONS", friends.text)
            self.assertIn("cryptoAgentDirectConversation", friends.text)
            self.assertIn("conversationNotificationPoll", friends.text)

            profiles = client.get("/profiles")
            self.assertEqual(profiles.status_code, 200)
            self.assertIn('id="profileForm"', profiles.text)
            self.assertIn('id="globalDirectChatDock"', profiles.text)
            self.assertIn('id="globalDirectChatPanels"', profiles.text)
            self.assertIn('id="profileDisplayName"', profiles.text)
            self.assertIn('id="profileBio"', profiles.text)
            self.assertIn('id="profilePicture"', profiles.text)
            self.assertIn('id="themeLightBtn"', profiles.text)
            self.assertIn('id="tabPreferences"', profiles.text)
            self.assertIn('id="aiProfileForm"', profiles.text)
            self.assertIn('id="aiExperienceLevel"', profiles.text)
            self.assertIn('id="aiCommunicationStyle"', profiles.text)
            self.assertIn('id="aiFavoriteAssets"', profiles.text)
            self.assertIn("AI AGENT PERSONALIZATION", profiles.text)
            self.assertIn("API_USER_ME", profiles.text)

            profile = client.get("/profile")
            self.assertEqual(profile.status_code, 200)
            self.assertIn('id="profileForm"', profile.text)

            login = client.get("/login")
            self.assertEqual(login.status_code, 200)
            self.assertIn('id="authForm"', login.text)
            self.assertIn("/api/auth/login", login.text)
            self.assertIn("/api/dev/reload-version", login.text)

            register = client.get("/register")
            self.assertEqual(register.status_code, 200)
            self.assertIn('id="authForm"', register.text)
            self.assertIn("/api/auth/register", register.text)

            reload_version = client.get("/api/dev/reload-version")
            self.assertEqual(reload_version.status_code, 200)
            self.assertTrue(reload_version.json()["enabled"])
            self.assertTrue(reload_version.json()["version"])

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

    def test_portfolio_parse_holdings_refresh_and_scope(self):
        market_payload = {
            "bitcoin": {"usd": 70000, "usd_24h_change": 2.0, "source": "test"},
            "ethereum": {"usd": 3000, "usd_24h_change": -1.0, "source": "test"},
        }

        with TestClient(self.app) as client, patch("backend.app.fetch_market_prices", return_value=market_payload) as prices:
            unauthenticated = client.get("/api/portfolio")
            self.assertEqual(unauthenticated.status_code, 401)
            self.assertEqual(client.post("/api/portfolio/parse", json={"text": "1 BTC at 1"}).status_code, 401)
            self.assertEqual(
                client.post(
                    "/api/portfolio/holdings",
                    json={"holdings": [{"symbol": "BTC", "quantity": 1, "averageCostUsd": 1}]},
                ).status_code,
                401,
            )
            self.assertEqual(client.post("/api/portfolio/refresh").status_code, 401)
            self.assertEqual(client.delete("/api/portfolio/holdings/BTC").status_code, 401)

            owner_register = client.post(
                "/api/auth/register",
                json={
                    "username": "portfolio_owner",
                    "email": "portfolio-owner@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(owner_register.status_code, 201)
            owner_headers = {"Authorization": f"Bearer {owner_register.json()['accessToken']}"}

            parsed = client.post(
                "/api/portfolio/parse",
                json={"text": "I bought 0.5 BTC at 65000 and 3 ETH at 3200 and 4 ABC at 1"},
                headers=owner_headers,
            )
            self.assertEqual(parsed.status_code, 200)
            draft = parsed.json()["draft"]
            valid_rows = [row for row in draft if row["valid"]]
            invalid_rows = [row for row in draft if not row["valid"]]
            self.assertEqual([row["symbol"] for row in valid_rows], ["BTC", "ETH"])
            self.assertEqual(invalid_rows[0]["symbol"], "ABC")
            self.assertIn("Unsupported crypto symbol", invalid_rows[0]["error"])

            saved = client.post(
                "/api/portfolio/holdings",
                json={
                    "holdings": [
                        {
                            "symbol": row["symbol"],
                            "quantity": row["quantity"],
                            "averageCostUsd": row["averageCostUsd"],
                        }
                        for row in valid_rows
                    ]
                },
                headers=owner_headers,
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(len(saved.json()["holdings"]), 2)
            self.assertEqual(len(saved.json()["snapshots"]), 1)
            self.assertAlmostEqual(saved.json()["summary"]["totalCostUsd"], 42100.0)

            refresh = client.post("/api/portfolio/refresh", headers=owner_headers)
            self.assertEqual(refresh.status_code, 200)
            prices.assert_called()
            refreshed = refresh.json()
            by_symbol = {holding["symbol"]: holding for holding in refreshed["holdings"]}
            self.assertAlmostEqual(by_symbol["BTC"]["currentValueUsd"], 35000.0)
            self.assertAlmostEqual(by_symbol["ETH"]["currentValueUsd"], 9000.0)
            self.assertAlmostEqual(refreshed["summary"]["totalCostUsd"], 42100.0)
            self.assertAlmostEqual(refreshed["summary"]["totalValueUsd"], 44000.0)
            self.assertAlmostEqual(refreshed["summary"]["totalPlUsd"], 1900.0)
            self.assertGreaterEqual(len(refreshed["snapshots"]), 2)

            other_register = client.post(
                "/api/auth/register",
                json={
                    "username": "portfolio_other",
                    "email": "portfolio-other@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(other_register.status_code, 201)
            other_headers = {"Authorization": f"Bearer {other_register.json()['accessToken']}"}
            other_portfolio = client.get("/api/portfolio", headers=other_headers)
            self.assertEqual(other_portfolio.status_code, 200)
            self.assertEqual(other_portfolio.json()["holdings"], [])

            deleted = client.delete("/api/portfolio/holdings/BTC", headers=owner_headers)
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual([holding["symbol"] for holding in deleted.json()["holdings"]], ["ETH"])

    def test_note_retrieval_is_user_scoped_and_passed_to_agent(self):
        from backend.ai.retrieval import retrieve_user_notes

        with TestClient(self.app) as client, patch("backend.app.run_agent", return_value="personalized reply") as run_agent:
            rag_register = client.post(
                "/api/auth/register",
                json={
                    "username": "raguser",
                    "email": "raguser@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(rag_register.status_code, 201)
            rag_user = rag_register.json()["user"]
            rag_headers = {"Authorization": f"Bearer {rag_register.json()['accessToken']}"}

            rag_profile = client.patch(
                "/api/users/me",
                json={
                    "displayName": "RAG Analyst",
                    "bio": "Technical BTC trader focused on support and invalidation.",
                    "picture": "",
                    "aiProfile": {
                        "experienceLevel": "advanced",
                        "communicationStyle": "technical",
                        "riskProfile": "conservative",
                        "preferredDepth": "detailed",
                        "favoriteAssets": "BTC",
                        "goals": "Respect invalidation before entering trades.",
                    },
                },
                headers=rag_headers,
            )
            self.assertEqual(rag_profile.status_code, 200)

            rag_note = client.post(
                "/api/notes",
                json={"content": "rag-only BTC support at 60000, watching for rebound"},
                headers=rag_headers,
            )
            self.assertEqual(rag_note.status_code, 201)

            other_register = client.post(
                "/api/auth/register",
                json={
                    "username": "ragother",
                    "email": "ragother@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(other_register.status_code, 201)
            other_user = other_register.json()["user"]
            other_headers = {"Authorization": f"Bearer {other_register.json()['accessToken']}"}
            other_note = client.post(
                "/api/notes",
                json={"content": "other-only BTC liquidation note must not leak"},
                headers=other_headers,
            )
            self.assertEqual(other_note.status_code, 201)

            rag_results = retrieve_user_notes(rag_user["id"], "BTC support rebound", limit=4)
            self.assertTrue(any("rag-only BTC support" in note["content"] for note in rag_results))
            self.assertFalse(any("other-only" in note["content"] for note in rag_results))

            other_results = retrieve_user_notes(other_user["id"], "BTC support rebound", limit=4)
            self.assertTrue(any("other-only BTC liquidation" in note["content"] for note in other_results))
            self.assertFalse(any("rag-only" in note["content"] for note in other_results))

            chat = client.post(
                "/api/chat",
                json={"message": "What was my BTC support rebound plan?", "mode": "instant"},
                headers=rag_headers,
            )
            self.assertEqual(chat.status_code, 200)
            retrieved_notes = run_agent.call_args.kwargs["retrieved_notes"]
            ai_profile = run_agent.call_args.kwargs["ai_profile"]
            self.assertTrue(any("rag-only BTC support" in note["content"] for note in retrieved_notes))
            self.assertFalse(any("other-only" in note["content"] for note in retrieved_notes))
            self.assertEqual(ai_profile["displayName"], "RAG Analyst")
            self.assertEqual(ai_profile["communicationStyle"], "technical")
            self.assertEqual(ai_profile["riskProfile"], "conservative")
            self.assertEqual(run_agent.call_args.kwargs["recent_activity"], [])

    def test_agent_greeting_uses_user_profile_context(self):
        with TestClient(self.app) as client:
            register = client.post(
                "/api/auth/register",
                json={
                    "username": "greetinguser",
                    "email": "greetinguser@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(register.status_code, 201)
            headers = {"Authorization": f"Bearer {register.json()['accessToken']}"}

            profile = client.patch(
                "/api/users/me",
                json={
                    "displayName": "Greeting Trader",
                    "bio": "",
                    "picture": "",
                    "aiProfile": {
                        "experienceLevel": "intermediate",
                        "communicationStyle": "direct",
                        "riskProfile": "balanced",
                        "preferredDepth": "normal",
                        "favoriteAssets": "BTC, SOL",
                        "goals": "Build cleaner swing trade plans.",
                    },
                },
                headers=headers,
            )
            self.assertEqual(profile.status_code, 200)

            greeting = client.get("/api/agent/greeting", headers=headers)
            self.assertEqual(greeting.status_code, 200)
            self.assertIn("BTC, SOL", greeting.json()["message"])
            self.assertEqual(greeting.json()["source"], "ai-profile")
            self.assertEqual(greeting.json()["style"], "direct")

            executive_register = client.post(
                "/api/auth/register",
                json={
                    "username": "greetingexec",
                    "email": "greetingexec@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(executive_register.status_code, 201)
            executive_headers = {"Authorization": f"Bearer {executive_register.json()['accessToken']}"}
            executive_profile = client.patch(
                "/api/users/me",
                json={
                    "displayName": "Executive Trader",
                    "bio": "",
                    "picture": "",
                    "aiProfile": {
                        "experienceLevel": "advanced",
                        "communicationStyle": "executive",
                        "riskProfile": "balanced",
                        "preferredDepth": "short",
                        "favoriteAssets": "BTC, SOL",
                        "goals": "Build cleaner swing trade plans.",
                    },
                },
                headers=executive_headers,
            )
            self.assertEqual(executive_profile.status_code, 200)
            executive_greeting = client.get("/api/agent/greeting", headers=executive_headers)
            self.assertEqual(executive_greeting.status_code, 200)
            self.assertEqual(executive_greeting.json()["style"], "executive")
            self.assertIn("BTC, SOL", executive_greeting.json()["message"])
            self.assertNotEqual(greeting.json()["message"], executive_greeting.json()["message"])

    def test_recommendations_follow_user_context(self):
        with TestClient(self.app) as client:
            anonymous = client.get("/api/recommendations")
            self.assertEqual(anonymous.status_code, 401)

            register = client.post(
                "/api/auth/register",
                json={
                    "username": "recommenduser",
                    "email": "recommenduser@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(register.status_code, 201)
            headers = {"Authorization": f"Bearer {register.json()['accessToken']}"}

            defaults = client.get("/api/notifications", headers=headers)
            self.assertEqual(defaults.status_code, 200)
            fresh_recommendations = client.get("/api/recommendations?limit=6", headers=headers)
            self.assertEqual(fresh_recommendations.status_code, 200)
            self.assertEqual(fresh_recommendations.json()["recommendations"], [])

            with patch("backend.app.run_agent", return_value="generic reply"):
                generated_prompt_chat = client.post(
                    "/api/chat",
                    json={"message": "BTC price and trend analysis", "mode": "instant"},
                    headers=headers,
                )
            self.assertEqual(generated_prompt_chat.status_code, 200)
            generated_prompt_recommendations = client.get("/api/recommendations?limit=6", headers=headers)
            self.assertEqual(generated_prompt_recommendations.status_code, 200)
            self.assertEqual(generated_prompt_recommendations.json()["recommendations"], [])

            note = client.post(
                "/api/notes",
                json={"content": "BNB breakout plan, watching invalidation risk and support reclaim."},
                headers=headers,
            )
            self.assertEqual(note.status_code, 201)

            recommendations = client.get("/api/recommendations?limit=6", headers=headers)
            self.assertEqual(recommendations.status_code, 200)
            items = recommendations.json()["recommendations"]
            self.assertGreaterEqual(len(items), 1)
            combined = " ".join(f"{item['label']} {item['prompt']}" for item in items)
            self.assertIn("BNB", combined)
            self.assertEqual(items[0]["source"], "personalized-symbol")

            other_register = client.post(
                "/api/auth/register",
                json={
                    "username": "recommendother",
                    "email": "recommendother@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(other_register.status_code, 201)
            other_headers = {"Authorization": f"Bearer {other_register.json()['accessToken']}"}
            other_defaults = client.get("/api/notifications", headers=other_headers)
            self.assertEqual(other_defaults.status_code, 200)
            other_note = client.post(
                "/api/notes",
                json={"content": "SOL validator rotation and staking yield plan."},
                headers=other_headers,
            )
            self.assertEqual(other_note.status_code, 201)
            other_recommendations = client.get("/api/recommendations?limit=6", headers=other_headers)
            self.assertEqual(other_recommendations.status_code, 200)
            other_items = other_recommendations.json()["recommendations"]
            other_combined = " ".join(f"{item['label']} {item['prompt']}" for item in other_items)
            self.assertIn("SOL", other_combined)
            self.assertNotEqual(items[0]["label"], other_items[0]["label"])

    def test_admin_pages_and_apis(self):
        with TestClient(self.app) as client:
            login = client.post("/api/auth/login", json={"login": "seed", "password": "password123"})
            token = login.json()["accessToken"]

            page = client.get("/admin")
            self.assertEqual(page.status_code, 200)
            self.assertIn("CMS Users, Sessions & Logs", page.text)
            self.assertIn("Admin Actions", page.text)

            users = client.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(users.status_code, 200)
            self.assertGreaterEqual(len(users.json()["users"]), 1)
            self.assertTrue(next(user for user in users.json()["users"] if user["username"] == "seed")["isAdmin"])

            logs = client.get("/api/admin/request-logs", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(logs.status_code, 200)
            self.assertIn("logs", logs.json())

    def test_admin_cms_user_lifecycle(self):
        with TestClient(self.app) as client:
            admin_login = client.post("/api/auth/login", json={"login": "seed", "password": "password123"})
            admin_token = admin_login.json()["accessToken"]
            admin_headers = {"Authorization": f"Bearer {admin_token}"}

            victim_register = client.post(
                "/api/auth/register",
                json={
                    "username": "cmsvictim",
                    "email": "cmsvictim@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(victim_register.status_code, 201)
            victim = victim_register.json()["user"]
            victim_token = victim_register.json()["accessToken"]
            victim_headers = {"Authorization": f"Bearer {victim_token}"}

            non_admin = client.get("/api/admin/users", headers=victim_headers)
            self.assertEqual(non_admin.status_code, 403)

            suspend = client.post(
                f"/api/admin/users/{victim['id']}/suspend",
                json={"reason": "test suspension"},
                headers=admin_headers,
            )
            self.assertEqual(suspend.status_code, 200)
            self.assertTrue(suspend.json()["user"]["disabledAt"])

            blocked_api = client.get("/api/notes", headers=victim_headers)
            self.assertEqual(blocked_api.status_code, 403)
            self.assertEqual(blocked_api.json()["error"], "Account disabled")

            blocked_login = client.post("/api/auth/login", json={"login": "cmsvictim", "password": "password123"})
            self.assertEqual(blocked_login.status_code, 403)

            unsuspend = client.post(f"/api/admin/users/{victim['id']}/unsuspend", headers=admin_headers)
            self.assertEqual(unsuspend.status_code, 200)
            self.assertIsNone(unsuspend.json()["user"]["disabledAt"])

            reset = client.post(
                f"/api/admin/users/{victim['id']}/reset-password",
                json={"password": "newpassword123"},
                headers=admin_headers,
            )
            self.assertEqual(reset.status_code, 200)

            old_login = client.post("/api/auth/login", json={"login": "cmsvictim", "password": "password123"})
            self.assertEqual(old_login.status_code, 401)

            new_login = client.post("/api/auth/login", json={"login": "cmsvictim", "password": "newpassword123"})
            self.assertEqual(new_login.status_code, 200)

            revoke = client.post(f"/api/admin/users/{victim['id']}/revoke-sessions", headers=admin_headers)
            self.assertEqual(revoke.status_code, 200)
            refresh_after_revoke = client.post("/api/auth/refresh")
            self.assertEqual(refresh_after_revoke.status_code, 401)

            self_delete = client.delete(f"/api/admin/users/{admin_login.json()['user']['id']}", headers=admin_headers)
            self.assertEqual(self_delete.status_code, 400)

            delete = client.delete(f"/api/admin/users/{victim['id']}", headers=admin_headers)
            self.assertEqual(delete.status_code, 200)
            deleted_login = client.post("/api/auth/login", json={"login": "cmsvictim", "password": "newpassword123"})
            self.assertEqual(deleted_login.status_code, 401)

            actions = client.get("/api/admin/actions", headers=admin_headers)
            self.assertEqual(actions.status_code, 200)
            action_names = {action["action"] for action in actions.json()["actions"]}
            self.assertTrue({"suspend_user", "unsuspend_user", "reset_password", "revoke_sessions", "delete_user"} <= action_names)

    def test_forum_topics_replies_and_ai_summary(self):
        with TestClient(self.app) as client, patch(
            "backend.forum.routes.summarize_forum_thread",
            return_value=("BTC thread summary", "test-summary-model"),
        ) as summarize:
            page = client.get("/forum")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Discussion Forum", page.text)
            self.assertIn("AI summarize", page.text)

            login = client.post("/api/auth/login", json={"login": "seed", "password": "password123"})
            token = login.json()["accessToken"]
            headers = {"Authorization": f"Bearer {token}"}

            unauth_topics = client.get("/api/forum/topics")
            self.assertEqual(unauth_topics.status_code, 401)

            created = client.post(
                "/api/forum/topics",
                json={"title": "BTC breakout setup", "body": "Watching BTC above 70000 with invalidation below 68000."},
                headers=headers,
            )
            self.assertEqual(created.status_code, 201)
            topic = created.json()["topic"]
            self.assertEqual(topic["title"], "BTC breakout setup")
            self.assertEqual(topic["replyCount"], 0)

            reply_user = client.post(
                "/api/auth/register",
                json={
                    "username": "forumreply",
                    "email": "forumreply@example.com",
                    "password": "password123",
                },
            )
            self.assertEqual(reply_user.status_code, 201)
            reply_headers = {"Authorization": f"Bearer {reply_user.json()['accessToken']}"}

            reply = client.post(
                f"/api/forum/topics/{topic['id']}/posts",
                json={"content": "I would wait for volume confirmation before chasing."},
                headers=reply_headers,
            )
            self.assertEqual(reply.status_code, 201)
            self.assertEqual(reply.json()["topic"]["replyCount"], 1)
            self.assertEqual(reply.json()["posts"][0]["author"]["username"], "forumreply")
            post_id = reply.json()["posts"][0]["id"]

            notifications = client.get("/api/notifications", headers=headers)
            self.assertEqual(notifications.status_code, 200)
            forum_events = [
                event
                for event in notifications.json()["events"]
                if event["eventType"] == "forum_reply"
            ]
            self.assertTrue(forum_events)
            self.assertEqual(forum_events[0]["linkUrl"], f"/forum?topic={topic['id']}")

            reaction = client.post(
                f"/api/forum/posts/{post_id}/reaction",
                json={"reaction": "fire"},
                headers=headers,
            )
            self.assertEqual(reaction.status_code, 200)
            reacted_post = next(post for post in reaction.json()["posts"] if post["id"] == post_id)
            self.assertEqual(reacted_post["reactionCounts"]["fire"], 1)
            self.assertEqual(reacted_post["myReaction"], "fire")

            removed_reaction = client.post(
                f"/api/forum/posts/{post_id}/reaction",
                json={"reaction": None},
                headers=headers,
            )
            self.assertEqual(removed_reaction.status_code, 200)
            unreacted_post = next(post for post in removed_reaction.json()["posts"] if post["id"] == post_id)
            self.assertNotIn("fire", unreacted_post["reactionCounts"])
            self.assertEqual(unreacted_post["myReaction"], "")

            topics = client.get("/api/forum/topics", headers=headers)
            self.assertEqual(topics.status_code, 200)
            self.assertTrue(any(item["title"] == "BTC breakout setup" for item in topics.json()["topics"]))

            detail = client.get(f"/api/forum/topics/{topic['id']}", headers=headers)
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["posts"][0]["content"], "I would wait for volume confirmation before chasing.")

            summary = client.post(f"/api/forum/topics/{topic['id']}/summary", headers=headers)
            self.assertEqual(summary.status_code, 200)
            self.assertEqual(summary.json()["summary"], "BTC thread summary")
            self.assertEqual(summary.json()["model"], "test-summary-model")
            self.assertEqual(summary.json()["topic"]["summary"], "BTC thread summary")
            summarize.assert_called_once()

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
            initial_unread = initial.json()["unreadCount"]

            checked = client.post("/api/notifications/check", headers=headers)
            self.assertEqual(checked.status_code, 200)
            self.assertEqual(len(checked.json()["created"]), 1)
            self.assertEqual(checked.json()["created"][0]["symbol"], "BTC")
            self.assertEqual(checked.json()["unreadCount"], initial_unread + 1)

            read = client.post("/api/notifications/read", headers=headers)
            self.assertEqual(read.status_code, 200)
            self.assertEqual(read.json()["unreadCount"], 0)

    def test_market_price_provider_fallbacks(self):
        from backend import app as backend_app

        class MockResponse:
            def __init__(self, payload=None, error=None):
                self.payload = payload or {}
                self.error = error

            def raise_for_status(self):
                if self.error:
                    raise self.error

            def json(self):
                return self.payload

        def fake_get(url, **kwargs):
            if "api.binance.com" in url or "api.binance.us" in url:
                return MockResponse(error=requests.HTTPError("binance unavailable"))
            if "api.exchange.coinbase.com/products/BTC-USD/stats" in url:
                return MockResponse({"last": "73000", "open": "70000"})
            if "api.coingecko.com" in url:
                return MockResponse({"binancecoin": {"usd": 620, "usd_24h_change": 3.4}})
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("backend.app.requests.get", side_effect=fake_get):
            prices = backend_app.fetch_market_prices(["bitcoin", "binancecoin"])

        self.assertEqual(prices["bitcoin"]["source"], "coinbase")
        self.assertAlmostEqual(prices["bitcoin"]["usd"], 73000)
        self.assertAlmostEqual(prices["bitcoin"]["usd_24h_change"], (3000 / 70000) * 100)
        self.assertEqual(prices["binancecoin"]["source"], "coingecko")
        self.assertEqual(prices["binancecoin"]["usd_24h_change"], 3.4)

    def test_price_data_endpoint_returns_backend_market_rows(self):
        from backend import app as backend_app

        backend_app.market_price_cache["checked_at"] = 0
        backend_app.market_price_cache["prices"] = []
        market_payload = {
            "bitcoin": {"usd": 73000, "usd_24h_change": 2.5, "source": "test-provider"},
            "ethereum": {"usd": 2100, "usd_24h_change": -1.2, "source": "test-provider"},
        }

        with TestClient(self.app) as client, patch("backend.app.fetch_market_prices", return_value=market_payload):
            res = client.get("/api/price-data")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["live"])
        rows = {row["symbol"]: row for row in res.json()["prices"]}
        self.assertEqual(rows["BTC"]["price"], 73000)
        self.assertEqual(rows["BTC"]["changePercent"], 2.5)
        self.assertEqual(rows["BTC"]["source"], "test-provider")
        self.assertEqual(rows["ETH"]["usd_24h_change"], -1.2)

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

            public_charlie = client.get("/api/users/socialcharlie", headers=seed_headers)
            self.assertEqual(public_charlie.status_code, 200)
            self.assertEqual(public_charlie.json()["user"]["username"], "socialcharlie")
            self.assertEqual(public_charlie.json()["user"]["id"], charlie_id)

            public_charlie_by_id = client.get(f"/api/users/{charlie_id}", headers=seed_headers)
            self.assertEqual(public_charlie_by_id.status_code, 200)
            self.assertEqual(public_charlie_by_id.json()["user"]["username"], "socialcharlie")

            missing_public_profile = client.get("/api/users/not-a-real-user", headers=seed_headers)
            self.assertEqual(missing_public_profile.status_code, 404)

            public_profile_page = client.get("/profiles/socialcharlie")
            self.assertEqual(public_profile_page.status_code, 200)
            self.assertIn("PROFILE_REF", public_profile_page.text)
            self.assertIn("publicProfileNote", public_profile_page.text)
            self.assertIn('id="addFriendBtn"', public_profile_page.text)
            self.assertNotIn('id="profilePicture"', public_profile_page.text)
            self.assertNotIn('id="profileForm"', public_profile_page.text)
            self.assertNotIn('id="aiProfileForm"', public_profile_page.text)

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

            with client.websocket_connect(
                f"/api/conversations/{conversation_id}/ws?token={bob_token}"
            ) as websocket:
                live_message = client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={"content": "Realtime BTC update."},
                    headers=seed_headers,
                )
                self.assertEqual(live_message.status_code, 201)
                event = websocket.receive_json()
                self.assertEqual(event["type"], "message")
                self.assertEqual(event["message"]["content"], "Realtime BTC update.")

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
            bob_message = next(
                message
                for message in bob_messages.json()["messages"]
                if message["content"] == "BTC moved fast today."
            )
            self.assertFalse(bob_message["isOwn"])

            bob_conversations = client.get("/api/conversations", headers=bob_headers)
            self.assertEqual(bob_conversations.status_code, 200)
            self.assertEqual(bob_conversations.json()["conversations"][0]["unreadCount"], 2)

            bob_notifications = client.get("/api/notifications", headers=bob_headers)
            self.assertEqual(bob_notifications.status_code, 200)
            direct_events = [
                event
                for event in bob_notifications.json()["events"]
                if event["eventType"] == "direct_message"
            ]
            self.assertFalse(direct_events)

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

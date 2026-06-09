import re
from collections import Counter

from backend.auth.store import auth_connection


SYMBOL_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "BNB": "BNB",
    "XRP": "XRP",
    "ADA": "Cardano",
    "AVAX": "Avalanche",
    "DOGE": "Dogecoin",
    "LINK": "Chainlink",
    "DOT": "Polkadot",
    "LTC": "Litecoin",
}

THEME_PATTERNS = {
    "DeFi": ["defi", "yield", "liquidity", "dex", "lending"],
    "AI coins": [" ai ", "agent", "compute", "gpu", "tao", "render"],
    "memecoins": ["meme", "doge", "shib", "pepe"],
    "staking": ["stake", "staking", "validator", "yield"],
    "risk": ["risk", "invalidation", "stop", "drawdown", "liquidation"],
    "news": ["news", "headline", "event", "cpi", "fed", "etf"],
}

GENERATED_PROMPT_PREFIXES = (
    "use my notes and recent context to review my ",
    "what are the biggest risks in my current ",
    "use my saved context to analyze my ",
    "summarize the forum discussions i have been active in",
    "find the most important trading insights from my notebook",
)

GENERATED_PROMPTS = {
    "btc price and trend analysis",
    "latest crypto news today",
    "top gainers and losers today",
    "help me build a risk checklist for my current crypto watchlist.",
    "build a crypto watchlist from my recent interests.",
}


def is_generated_prompt(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in GENERATED_PROMPTS or any(
        normalized.startswith(prefix) for prefix in GENERATED_PROMPT_PREFIXES
    )


def fetch_recommendation_context(user_id: int | str) -> dict:
    with auth_connection() as conn:
        note_rows = conn.execute(
            """
            SELECT content
            FROM notes
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 30
            """,
            (str(user_id),),
        ).fetchall()
        request_rows = conn.execute(
            """
            SELECT message
            FROM user_requests
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 30
            """,
            (str(user_id),),
        ).fetchall()
        forum_rows = conn.execute(
            """
            SELECT text
            FROM (
                SELECT title || ' ' || body AS text, created_at
                FROM forum_topics
                WHERE user_id = ?
                UNION ALL
                SELECT content AS text, created_at
                FROM forum_posts
                WHERE user_id = ?
            )
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (str(user_id), str(user_id)),
        ).fetchall()
        message_rows = conn.execute(
            """
            SELECT m.content
            FROM messages m
            JOIN conversation_participants cp ON cp.conversation_id = m.conversation_id
            WHERE cp.user_id = ?
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT 30
            """,
            (str(user_id),),
        ).fetchall()
        alert_rows = conn.execute(
            """
            SELECT symbol
            FROM notification_settings
            WHERE user_id = ?
              AND enabled = 1
              AND (updated_at > created_at OR last_notified_at IS NOT NULL)
            ORDER BY updated_at DESC, id DESC
            LIMIT 10
            """,
            (str(user_id),),
        ).fetchall()

    return {
        "notes": [row["content"] for row in note_rows],
        "requests": [row["message"] for row in request_rows if not is_generated_prompt(row["message"])],
        "forum": [row["text"] for row in forum_rows],
        "messages": [row["content"] for row in message_rows],
        "alerts": [row["symbol"] for row in alert_rows],
    }


def score_symbols(context: dict) -> Counter:
    scores = Counter()
    weighted_sources = [
        ("notes", 4),
        ("requests", 3),
        ("forum", 2),
        ("messages", 1),
    ]
    for source, weight in weighted_sources:
        text = "\n".join(context[source]).upper()
        for symbol, name in SYMBOL_NAMES.items():
            symbol_hits = len(re.findall(rf"\b{re.escape(symbol)}\b", text))
            name_hits = text.count(name.upper())
            if symbol_hits or name_hits:
                scores[symbol] += (symbol_hits + name_hits) * weight

    for symbol in context["alerts"]:
        normalized = (symbol or "").upper()
        if normalized in SYMBOL_NAMES:
            scores[normalized] += 2
    return scores


def score_themes(context: dict) -> Counter:
    scores = Counter()
    text = (" " + "\n".join(context["notes"] + context["requests"] + context["forum"] + context["messages"]) + " ").lower()
    for theme, patterns in THEME_PATTERNS.items():
        score = sum(text.count(pattern) for pattern in patterns)
        if score:
            scores[theme] = score
    return scores


def add_unique(items: list[dict], seen: set[str], label: str, prompt: str, source: str) -> None:
    key = label.lower()
    if key in seen:
        return
    seen.add(key)
    items.append({"label": label, "prompt": prompt, "source": source})


def personalized_recommendations(user_id: int | str, limit: int = 6) -> list[dict]:
    context = fetch_recommendation_context(user_id)
    symbol_scores = score_symbols(context)
    theme_scores = score_themes(context)
    items: list[dict] = []
    seen: set[str] = set()

    for symbol, _ in symbol_scores.most_common(3):
        add_unique(
            items,
            seen,
            f"{symbol} plan",
            f"Use my notes and recent context to review my {symbol} trading plan.",
            "personalized-symbol",
        )
        add_unique(
            items,
            seen,
            f"{symbol} risk",
            f"What are the biggest risks in my current {symbol} setup?",
            "personalized-symbol",
        )
        if len(items) >= limit:
            break

    for theme, _ in theme_scores.most_common(3):
        add_unique(
            items,
            seen,
            theme,
            f"Use my saved context to analyze my {theme} ideas.",
            "personalized-theme",
        )
        if len(items) >= limit:
            break

    if context["forum"]:
        add_unique(
            items,
            seen,
            "forum recap",
            "Summarize the forum discussions I have been active in.",
            "forum-activity",
        )

    if context["notes"]:
        add_unique(
            items,
            seen,
            "note insights",
            "Find the most important trading insights from my notebook.",
            "notebook",
        )

    return items[: max(1, min(int(limit or 6), 10))]

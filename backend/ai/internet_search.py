import os
from functools import lru_cache
from urllib.parse import urlparse

from tavily import TavilyClient


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@lru_cache(maxsize=1)
def tavily_client() -> TavilyClient | None:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return None
    return TavilyClient(api_key=api_key)


def source_label(url: str, title: str = "") -> str:
    hostname = urlparse(url or "").hostname or ""
    hostname = hostname.removeprefix("www.")
    if hostname:
        parts = hostname.split(".")
        if len(parts) >= 2:
            return parts[-2].replace("-", " ").title()
        return hostname.replace("-", " ").title()
    return (title or "Source")[:40]


def search_crypto_news(query: str) -> str:
    """Search recent crypto news and return concise results with source links."""
    client = tavily_client()
    if not client:
        return "Error: TAVILY_API_KEY not configured in .env"

    search_query = f"crypto {query}".strip()
    max_results = max(1, min(env_int("TAVILY_MAX_RESULTS", 3), 8))
    search_depth = os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip().lower() or "basic"
    include_raw_content = env_bool("TAVILY_INCLUDE_RAW_CONTENT", False)

    try:
        response = client.search(
            query=search_query,
            search_depth=search_depth,
            max_results=max_results,
            include_answer=True,
            include_raw_content=include_raw_content,
            topic="news",
        )
    except Exception as exc:
        return f"Error connecting to Tavily API: {exc}"

    answer = response.get("answer", "") or ""
    results = response.get("results", []) or []
    if not results and not answer:
        return f"Sorry, I couldn't find recent news about '{query}'."

    lines = [
        f"Search results for '{query}':",
        "Use the Source links as compact inline citations after relevant claims.",
    ]
    if answer:
        lines.extend(["", f"Summary: {answer}"])

    if results:
        lines.extend(["", "Sources:"])
        for index, item in enumerate(results, start=1):
            title = item.get("title") or "Untitled source"
            url = item.get("url") or ""
            content = (item.get("content") or "").strip()
            label = source_label(url, title)
            if url:
                lines.append(f"{index}. Title: {title}")
                lines.append(f"   Source: [{label}]({url})")
            else:
                lines.append(f"{index}. {title}")
            if content:
                lines.append(f"   Brief: {content[:240]}")

    return "\n".join(lines)

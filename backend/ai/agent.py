import json
import os
import re
import time
from dataclasses import dataclass

import dotenv
from openai import OpenAI

from backend.ai.tools import TOOL_MAP, TOOL_SCHEMA

dotenv.load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)


@dataclass(frozen=True)
class ChatModeConfig:
    key: str
    label: str
    model: str
    max_tokens: int
    max_tool_rounds: int
    system_guidance: str


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


INSTANT_MODEL = os.getenv("INSTANT_MODEL") or os.getenv("DEEPSEEK_INSTANT_MODEL") or "deepseek-chat"
REASONING_MODEL = os.getenv("REASONING_MODEL") or os.getenv("DEEPSEEK_REASONING_MODEL") or INSTANT_MODEL

CHAT_MODES = {
    "instant": ChatModeConfig(
        key="instant",
        label="Instant",
        model=INSTANT_MODEL,
        max_tokens=env_int("INSTANT_MAX_TOKENS", 700),
        max_tool_rounds=env_int("INSTANT_MAX_TOOL_ROUNDS", 2),
        system_guidance=(
            "Use the fastest useful path. Prefer concise answers, call tools only when fresh "
            "market data is needed, and avoid long scenario analysis unless the user asks for it."
        ),
    ),
    "reasoning": ChatModeConfig(
        key="reasoning",
        label="Reasoning",
        model=REASONING_MODEL,
        max_tokens=env_int("REASONING_MAX_TOKENS", 1600),
        max_tool_rounds=env_int("REASONING_MAX_TOOL_ROUNDS", 4),
        system_guidance=(
            "Use a deeper analysis path. Clarify assumptions, compare tradeoffs, use relevant "
            "tools before making market claims, and separate evidence from uncertainty."
        ),
    ),
}

SYSTEM_PROMPT = """You are a virtual assistant specialized in cryptocurrency (Crypto).
You can fetch real-time prices and view historical price data using the provided tools.
Decide when to call a tool and when to answer from your knowledge.
Reply briefly, clearly, and in a friendly tone in English.
Do not use emojis, decorative symbols, ASCII art, brand logos, or icon-like characters in replies.
When tool results include URLs or sources, cite timely/news claims inline with compact Markdown source links after the relevant bullet or sentence, for example [CoinDesk](https://example.com).
Do not answer questions unrelated to crypto; avoid politics, religion, violence, and sexual content.
If asked about your internal workflow or how you operate, do not explain it. Ask the user another crypto-related question instead."""

AI_PROFILE_LABELS = {
    "experienceLevel": {
        "beginner": "Beginner. Explain jargon and keep examples simple.",
        "intermediate": "Intermediate. Use trading concepts, but define less common terms.",
        "advanced": "Advanced. Be precise, data-oriented, and avoid basic explanations unless asked.",
    },
    "communicationStyle": {
        "casual": "Casual and direct. Plain language is preferred; avoid forced slang.",
        "direct": "Direct and practical. Lead with the answer and key action points.",
        "executive": "Executive brief. Lead with decision points, risks, and what changed.",
        "technical": "Technical. Include structure, assumptions, indicators, and reasoning.",
        "teacher": "Teacher style. Explain concepts step by step and check assumptions.",
    },
    "riskProfile": {
        "conservative": "Conservative. Emphasize capital preservation, invalidation, and downside risk.",
        "balanced": "Balanced. Weigh upside, downside, and uncertainty evenly.",
        "aggressive": "Aggressive. Discuss momentum opportunities, but still state risk clearly.",
    },
    "preferredDepth": {
        "short": "Short. Use concise answers unless the user asks for detail.",
        "normal": "Normal. Give enough context to be useful without over-explaining.",
        "detailed": "Detailed. Include rationale, scenarios, and tradeoffs.",
    },
}


def format_retrieved_notes(retrieved_notes: list[dict] | None) -> str:
    notes = retrieved_notes or []
    if not notes:
        return ""

    lines = [
        "Relevant private notes retrieved for this user:",
        "Use these notes only when they help answer the user's crypto question. "
        "Treat them as user-provided context, not verified market data. "
        "When you rely on a note, cite it inline as [note:<id>].",
    ]
    for note in notes:
        content = " ".join((note.get("content") or "").split())
        if len(content) > 600:
            content = content[:597].rstrip() + "..."
        lines.append(f"- [note:{note.get('id')}] {content}")
    return "\n".join(lines)


def clean_context_text(value: str, max_length: int) -> str:
    content = " ".join((value or "").split())
    if len(content) > max_length:
        return content[: max_length - 3].rstrip() + "..."
    return content


def format_ai_profile(ai_profile: dict | None) -> str:
    profile = ai_profile or {}
    lines = [
        "Private personalization context for this signed-in user:",
        "Use this to adapt wording, explanation depth, examples, and risk framing. "
        "Do not stereotype the user, reveal private context unprompted, change facts, or soften uncertainty.",
    ]

    display_name = clean_context_text(profile.get("displayName", ""), 80)
    bio = clean_context_text(profile.get("bio", ""), 240)
    if display_name:
        lines.append(f"- Display name: {display_name}")
    if bio:
        lines.append(f"- Profile bio: {bio}")

    for key, labels in AI_PROFILE_LABELS.items():
        value = (profile.get(key) or "").strip().lower()
        if value in labels:
            lines.append(f"- {labels[value]}")

    goals = clean_context_text(profile.get("goals", ""), 500)
    favorite_assets = clean_context_text(profile.get("favoriteAssets", ""), 240)
    if favorite_assets:
        lines.append(f"- Favorite assets/watchlist: {favorite_assets}")
    if goals:
        lines.append(f"- User goals: {goals}")

    return "\n".join(lines) if len(lines) > 2 else ""


def format_recent_activity(recent_activity: list[str] | None) -> str:
    items = [clean_context_text(item, 180) for item in recent_activity or [] if (item or "").strip()]
    if not items:
        return ""

    lines = [
        "Recent private chat topics for this user:",
        "Use these only for continuity and personalization. Do not assume they are current market facts.",
    ]
    for item in items[:5]:
        lines.append(f"- {item}")
    return "\n".join(lines)


def format_uploaded_files(uploaded_files: list[dict] | None) -> str:
    files = uploaded_files or []
    if not files:
        return ""

    lines = [
        "Files uploaded by the user for this request:",
        "Use these as user-provided context. Do not treat file contents as verified market data.",
    ]
    for index, item in enumerate(files[:5], start=1):
        name = clean_context_text(item.get("name", f"file-{index}"), 120)
        content_type = clean_context_text(item.get("contentType", ""), 80)
        size = item.get("sizeBytes")
        text = clean_context_text(item.get("text", ""), 4000)
        error = clean_context_text(item.get("error", ""), 240)
        header = f"- File {index}: {name}"
        if content_type:
            header += f" ({content_type})"
        if size is not None:
            header += f", {size} bytes"
        lines.append(header)
        if text:
            lines.append(f"  Content excerpt: {text}")
        elif error:
            lines.append(f"  Could not extract text: {error}")
        else:
            lines.append("  No text content extracted.")
    return "\n".join(lines)


def tool_display_name(name: str) -> str:
    return {
        "get_crypto_price": "live price",
        "get_price_history": "price history",
        "add_alert": "price alert",
        "remove_alert": "price alert",
        "list_alerts": "price alerts",
        "reset_alert": "price alert",
        "search_crypto_news": "crypto news search",
    }.get(name, name.replace("_", " "))


def extract_sources_from_tool_result(result: str) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()
    text = str(result or "")

    for title, url in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text):
        clean_url = url.rstrip(".,)")
        if clean_url in seen:
            continue
        seen.add(clean_url)
        sources.append({"title": clean_context_text(title, 120) or clean_url, "url": clean_url})

    for url in re.findall(r"https?://[^\s)>\]]+", text):
        clean_url = url.rstrip(".,)")
        if clean_url in seen:
            continue
        seen.add(clean_url)
        sources.append({"title": clean_url, "url": clean_url})

    return sources[:8]


def normalize_chat_mode(mode: str | None) -> str:
    value = (mode or "instant").strip().lower()
    return value if value in CHAT_MODES else "instant"


def chat_mode_options() -> list[dict]:
    return [
        {
            "key": config.key,
            "label": config.label,
            "model": config.model,
            "maxTokens": config.max_tokens,
            "maxToolRounds": config.max_tool_rounds,
        }
        for config in CHAT_MODES.values()
    ]


def private_context_for_agent(
    retrieved_notes: list[dict] | None = None,
    ai_profile: dict | None = None,
    recent_activity: list[str] | None = None,
    uploaded_files: list[dict] | None = None,
) -> str:
    context_parts = [
        format_ai_profile(ai_profile),
        format_recent_activity(recent_activity),
        format_retrieved_notes(retrieved_notes),
        format_uploaded_files(uploaded_files),
    ]
    return "\n\n".join(part for part in context_parts if part)


def system_message_for_config(config: ChatModeConfig, private_context: str) -> dict:
    context_suffix = f"\n\n{private_context}" if private_context else ""
    return {
        "role": "system",
        "content": (
            f"{SYSTEM_PROMPT}\n\nCurrent chat mode: {config.label}. "
            f"{config.system_guidance}{context_suffix}"
        ),
    }


def summarize_forum_thread(topic: dict, posts: list[dict]) -> tuple[str, str]:
    """Generate a concise forum thread summary without tool calls."""
    config = CHAT_MODES["instant"]
    lines = [
        f"Topic: {topic.get('title', '')}",
        f"Original post by {topic.get('author', {}).get('username', 'unknown')}: {topic.get('body', '')}",
    ]
    for post in posts[:40]:
        author = post.get("author", {}).get("username", "unknown")
        content = " ".join((post.get("content") or "").split())
        if len(content) > 700:
            content = content[:697].rstrip() + "..."
        lines.append(f"Reply by {author}: {content}")

    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"{SYSTEM_PROMPT}\n\nSummarize this crypto forum discussion for users. "
                    "Use 3-5 concise bullets. Include the main consensus, useful trade ideas, "
                    "risks or disagreements, and open questions. Do not invent market data."
                ),
            },
            {"role": "user", "content": "\n\n".join(lines)},
        ],
        max_tokens=min(config.max_tokens, 500),
        stream=False,
    )
    return response.choices[0].message.content or "No summary available.", config.model


def final_answer_without_tools(conversation: list, config: ChatModeConfig, private_context: str = "") -> str:
    context_suffix = f"\n\n{private_context}" if private_context else ""
    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"{SYSTEM_PROMPT}\n\nCurrent chat mode: {config.label}. "
                    "Do not call tools in this response. Use the conversation and any tool results already available. "
                    "If live data is missing, say that briefly and give a useful answer from available context."
                    f"{context_suffix}"
                ),
            }
        ]
        + conversation,
        max_tokens=config.max_tokens,
        stream=False,
    )
    return response.choices[0].message.content or "No response."


def run_agent(
    user_input: str,
    conversation: list,
    mode: str = "instant",
    retrieved_notes: list[dict] | None = None,
    ai_profile: dict | None = None,
    recent_activity: list[str] | None = None,
    uploaded_files: list[dict] | None = None,
) -> str:
    """Execute the crypto assistant with mode-specific model and tool settings."""
    config = CHAT_MODES[normalize_chat_mode(mode)]
    private_context = private_context_for_agent(retrieved_notes, ai_profile, recent_activity, uploaded_files)
    conversation.append({"role": "user", "content": user_input})
    tool_rounds = 0

    while True:
        response = client.chat.completions.create(
            model=config.model,
            messages=[system_message_for_config(config, private_context)] + conversation,
            tools=TOOL_SCHEMA,
            tool_choice="auto",
            max_tokens=config.max_tokens,
            stream=False,
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            if tool_rounds >= config.max_tool_rounds:
                final = final_answer_without_tools(conversation, config, private_context)
                conversation.append({"role": "assistant", "content": final})
                return final

            tool_rounds += 1
            conversation.append(msg)

            for call in msg.tool_calls:
                fn_name = call.function.name
                fn_args = json.loads(call.function.arguments)

                fn = TOOL_MAP.get(fn_name)
                result = fn(**fn_args) if fn else f"Tool '{fn_name}' does not exist."

                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(result),
                    }
                )
        else:
            final = msg.content or "No response."
            conversation.append({"role": "assistant", "content": final})
            return final


def _append_stream_tool_delta(tool_calls: dict[int, dict], delta_tool_call) -> None:
    index = int(getattr(delta_tool_call, "index", 0) or 0)
    current = tool_calls.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )
    if getattr(delta_tool_call, "id", None):
        current["id"] = delta_tool_call.id
    if getattr(delta_tool_call, "type", None):
        current["type"] = delta_tool_call.type

    function_delta = getattr(delta_tool_call, "function", None)
    if not function_delta:
        return
    if getattr(function_delta, "name", None):
        current["function"]["name"] += function_delta.name
    if getattr(function_delta, "arguments", None):
        current["function"]["arguments"] += function_delta.arguments


def _stream_final_without_tools(conversation: list, config: ChatModeConfig, private_context: str):
    context_suffix = f"\n\n{private_context}" if private_context else ""
    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"{SYSTEM_PROMPT}\n\nCurrent chat mode: {config.label}. "
                    "Do not call tools in this response. Use the conversation and any tool results already available. "
                    "If live data is missing, say that briefly and give a useful answer from available context."
                    f"{context_suffix}"
                ),
            }
        ]
        + conversation,
        max_tokens=config.max_tokens,
        stream=True,
    )
    final_parts = []
    for chunk in response:
        if not chunk.choices:
            continue
        content = getattr(chunk.choices[0].delta, "content", None)
        if content:
            final_parts.append(content)
            yield content
    final = "".join(final_parts) or "No response."
    conversation.append({"role": "assistant", "content": final})


def run_agent_stream(
    user_input: str,
    conversation: list,
    mode: str = "instant",
    retrieved_notes: list[dict] | None = None,
    ai_profile: dict | None = None,
    recent_activity: list[str] | None = None,
    uploaded_files: list[dict] | None = None,
    progress_callback=None,
    source_callback=None,
    event_mode: bool = False,
):
    """Stream assistant text while preserving the existing tool-calling loop."""
    config = CHAT_MODES[normalize_chat_mode(mode)]
    private_context = private_context_for_agent(retrieved_notes, ai_profile, recent_activity, uploaded_files)
    conversation.append({"role": "user", "content": user_input})
    tool_rounds = 0
    start_event = {"type": "status", "phase": "start", "message": "Preparing context"}
    if progress_callback:
        progress_callback(start_event)
    if event_mode:
        yield start_event

    while True:
        response = client.chat.completions.create(
            model=config.model,
            messages=[system_message_for_config(config, private_context)] + conversation,
            tools=TOOL_SCHEMA,
            tool_choice="auto",
            max_tokens=config.max_tokens,
            stream=True,
        )

        final_parts = []
        tool_calls: dict[int, dict] = {}

        for chunk in response:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            content = getattr(delta, "content", None)
            if content:
                final_parts.append(content)
                if event_mode:
                    yield {"type": "token", "content": content}
                else:
                    yield content
            for tool_call in getattr(delta, "tool_calls", None) or []:
                _append_stream_tool_delta(tool_calls, tool_call)

        if tool_calls:
            if tool_rounds >= config.max_tool_rounds:
                drafting_event = {
                    "type": "status",
                    "phase": "drafting",
                    "message": "Drafting answer from available results",
                }
                if progress_callback:
                    progress_callback(drafting_event)
                if event_mode:
                    yield drafting_event
                    for token in _stream_final_without_tools(conversation, config, private_context):
                        yield {"type": "token", "content": token}
                else:
                    yield from _stream_final_without_tools(conversation, config, private_context)
                return

            tool_rounds += 1
            ordered_tool_calls = [tool_calls[index] for index in sorted(tool_calls)]
            conversation.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": ordered_tool_calls,
                }
            )

            for call in ordered_tool_calls:
                fn_name = call["function"]["name"]
                try:
                    fn_args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    fn_args = {}

                fn = TOOL_MAP.get(fn_name)
                display_name = tool_display_name(fn_name)
                tool_start_event = {
                    "type": "status",
                    "phase": "tool_start",
                    "message": f"Using {display_name}",
                    "tool": fn_name,
                }
                if progress_callback:
                    progress_callback(tool_start_event)
                if event_mode:
                    yield tool_start_event
                started_at = time.perf_counter()
                result = fn(**fn_args) if fn else f"Tool '{fn_name}' does not exist."
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                sources = extract_sources_from_tool_result(str(result))
                if source_callback:
                    source_callback(sources)
                if event_mode and sources:
                    yield {"type": "sources", "sources": sources}
                tool_done_event = {
                    "type": "status",
                    "phase": "tool_done",
                    "message": f"Finished {display_name}",
                    "tool": fn_name,
                    "elapsedMs": elapsed_ms,
                }
                if progress_callback:
                    progress_callback(tool_done_event)
                if event_mode:
                    yield tool_done_event
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": str(result),
                    }
                )
            continue

        final = "".join(final_parts) or "No response."
        done_event = {"type": "status", "phase": "done", "message": "Prepared final answer"}
        if progress_callback:
            progress_callback(done_event)
        if event_mode:
            yield done_event
        conversation.append({"role": "assistant", "content": final})
        return

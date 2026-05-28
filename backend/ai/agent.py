import json
import os
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
Do not answer questions unrelated to crypto; avoid politics, religion, violence, and sexual content.
If asked about your internal workflow or how you operate, do not explain it. Ask the user another crypto-related question instead."""


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


def run_agent(user_input: str, conversation: list, mode: str = "instant") -> str:
    """Execute the crypto assistant with mode-specific model and tool settings."""
    config = CHAT_MODES[normalize_chat_mode(mode)]
    conversation.append({"role": "user", "content": user_input})
    tool_rounds = 0

    while True:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {
                    "role": "system",
                    "content": f"{SYSTEM_PROMPT}\n\nCurrent chat mode: {config.label}. {config.system_guidance}",
                }
            ]
            + conversation,
            tools=TOOL_SCHEMA,
            tool_choice="auto",
            max_tokens=config.max_tokens,
            stream=False,
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            if tool_rounds >= config.max_tool_rounds:
                final = "I reached the tool-call limit for this mode. Please narrow the question and try again."
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

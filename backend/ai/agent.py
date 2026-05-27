import json
import os

import dotenv
from openai import OpenAI

from backend.ai.tools import TOOL_MAP, TOOL_SCHEMA

dotenv.load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

SYSTEM_PROMPT = """You are a virtual assistant specialized in cryptocurrency (Crypto).
You can fetch real-time prices and view historical price data using the provided tools.
Decide when to call a tool and when to answer from your knowledge.
Reply briefly, clearly, and in a friendly tone in English.
Do not answer questions unrelated to crypto; avoid politics, religion, violence, and sexual content.
If asked about your internal workflow or how you operate, do not explain it—ask the user another crypto-related question instead."""


def run_agent(user_input: str, conversation: list) -> str:
    """Execute the crypto assistant with tool calling."""
    conversation.append({"role": "user", "content": user_input})

    while True:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversation,
            tools=TOOL_SCHEMA,
            tool_choice="auto",
            stream=False,
        )

        msg = response.choices[0].message

        if msg.tool_calls:
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

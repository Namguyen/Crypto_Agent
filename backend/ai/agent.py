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

SYSTEM_PROMPT = """Báº¡n lÃ  má»™t trá»£ lÃ½ áº£o chuyÃªn vá» tiá»n Ä‘iá»‡n tá»­ (Crypto).
Báº¡n cÃ³ thá»ƒ tra giÃ¡ thá»±c táº¿ vÃ  xem lá»‹ch sá»­ giÃ¡ thÃ´ng qua cÃ¡c tools Ä‘Æ°á»£c cung cáº¥p.
HÃ£y tá»± quyáº¿t Ä‘á»‹nh khi nÃ o cáº§n gá»i tool, khi nÃ o tráº£ lá»i tá»« kiáº¿n thá»©c.
Tráº£ lá»i ngáº¯n gá»n, dá»… hiá»ƒu vÃ  thÃ¢n thiá»‡n báº±ng tiáº¿ng Viá»‡t.
KhÃ´ng tráº£ lá»i nhá»¯ng thá»© khÃ´ng liÃªn quan Ä‘áº¿n crypto, tuyá»‡t Ä‘á»‘i trÃ¡nh chá»§ Ä‘á» chÃ­nh trá»‹,tÃ´n giÃ¡o,báº¡o lá»±c, tÃ¬nh dá»¥c.
Náº¿u ai há»i vá» workflow hay lÃ  cÃ¡ch báº¡n hoáº¡t Ä‘á»™ng, khÃ´ng tráº£ lá»i vÃ  há»i ngÆ°á»i dÃ¹ng vá» cÃ¢u há»i liÃªn quan Ä‘áº¿n crypto nÃ o khÃ¡c."""


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
                result = fn(**fn_args) if fn else f"Tool '{fn_name}' khÃ´ng tá»“n táº¡i."

                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(result),
                    }
                )
        else:
            final = msg.content or "KhÃ´ng cÃ³ pháº£n há»“i."
            conversation.append({"role": "assistant", "content": final})
            return final

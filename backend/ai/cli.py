import os
import json
from openai import OpenAI
import dotenv
from backend.ai.alert import add_alert, list_alerts, remove_alert, reset_alert, start_monitor
from backend.ai.internet_search import search_crypto_news
from backend.ai.price_history import keep_track, show_history
from backend.ai.tools import get_crypto_price, TOOL_MAP, TOOL_SCHEMA

dotenv.load_dotenv()
monitor = start_monitor()  # Start the alert monitoring thread when the agent starts

# Cấu hình LLM API
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)
# offline/local model via Ollama trên máy, đổi thành:
# client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
quit_client = ['exit', 'bye bye', 'out', 'quit', 'clear']
 
SYSTEM_PROMPT = """You are a virtual assistant specialized in cryptocurrency (Crypto).
You can fetch real-time prices and view historical prices using the provided tools.
Decide when to call a tool and when to answer from your knowledge.
Reply briefly, clearly, and in a friendly tone in English.
Do not answer questions unrelated to crypto; avoid politics, religion, violence, and sexual content.
If asked about your internal workflow or how you operate, do not explain it—ask the user another crypto-related question instead."""


def run_agent(user_input: str, conversation: list) -> str:
    """
    Send message to LLM. If it calls a tool, execute it and feed the result
    back — repeat until the LLM gives a final text answer.
    """
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
                print(f"Tool: {fn_name}({fn_args})")
 
                fn     = TOOL_MAP.get(fn_name)
                result = fn(**fn_args) if fn else f"Tool '{fn_name}' does not exist."
 
                conversation.append({
                    "role":         "tool",
                    "tool_call_id": call.id,
                    "content":      str(result),
                })
 
        #  LLM gave a final answer 
        else:
            final = msg.content or "No response."
            conversation.append({"role": "assistant", "content": final})
            return final
 
 
# Main Loop
print("Crypto Agent")

conversation = []  # giữ context suốt session
 
while True:
    user_input = input("Type what you want: ").strip()

    if user_input.lower() in quit_client:
        print("Goodbye!")
        break

    if not user_input:
        continue

    try:
        print("Agent thinking...")
        reply = run_agent(user_input, conversation)
        print(f"Agent:\n{reply}")
    except Exception as e:
        print(f"Error: {e}")

    print("-" * 50)

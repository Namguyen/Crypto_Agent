import os
import json
from openai import OpenAI
import dotenv
from tracker import keep_track, show_history
from tools import get_crypto_price, TOOL_SCHEMA, TOOL_MAP
dotenv.load_dotenv()


# Cấu hình LLM API
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)
# offline/local model via Ollama trên máy, đổi thành:
# client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
quit_client = ['exit', 'bye bye', 'out', 'quit', 'clear']
 
SYSTEM_PROMPT = """Bạn là một trợ lý ảo chuyên về tiền điện tử (Crypto).
Bạn có thể tra giá thực tế và xem lịch sử giá thông qua các tools được cung cấp.
Hãy tự quyết định khi nào cần gọi tool, khi nào trả lời từ kiến thức.
Trả lời ngắn gọn, dễ hiểu và thân thiện bằng tiếng Việt.
Không trả lời những thứ không liên quan đến crypto, tuyệt đối tránh chủ đề chính trị,tôn giáo"""


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
                result = fn(**fn_args) if fn else f"Tool '{fn_name}' không tồn tại."
 
                conversation.append({
                    "role":         "tool",
                    "tool_call_id": call.id,
                    "content":      str(result),
                })
 
        #  LLM gave a final answer 
        else:
            final = msg.content or " Không có phản hồi."
            conversation.append({"role": "assistant", "content": final})
            return final
 
 
# Main Loop
print(" Crypto Agent ")

conversation = []  # giữ context suốt session
 
while True:
    user_input = input("Type waht you want: ").strip()
 
    if user_input.lower() in quit_client:
        print("Tạm biệt!")
        break
 
    if not user_input:
        continue
 
    try:
        print("Agent đang suy nghĩ...")
        reply = run_agent(user_input, conversation)
        print(f"Agent:\n{reply}")
    except Exception as e:
        print(f"Lỗi: {e}")
 
    print("-" * 50)

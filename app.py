import os
import json
from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import dotenv
from tools import TOOL_SCHEMA, TOOL_MAP

dotenv.load_dotenv()

app = Flask(__name__)

# LLM client
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = """Bạn là một trợ lý ảo chuyên về tiền điện tử (Crypto).
Bạn có thể tra giá thực tế và xem lịch sử giá thông qua các tools được cung cấp.
Hãy tự quyết định khi nào cần gọi tool, khi nào trả lời từ kiến thức.
Trả lời ngắn gọn, dễ hiểu và thân thiện bằng tiếng Việt.
Không trả lời những thứ không liên quan đến crypto, tuyệt đối tránh chủ đề chính trị, tôn giáo"""


def run_agent(user_input: str, conversation: list) -> str:
    """Execute agent with tool calling."""
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
                result = fn(**fn_args) if fn else f"Tool '{fn_name}' không tồn tại."
                
                conversation.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })
        else:
            final = msg.content or "Không có phản hồi."
            conversation.append({"role": "assistant", "content": final})
            return final


# Store conversation in memory (reset on server restart)
conversation_history = []


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_input = data.get('message', '').strip()
    
    if not user_input:
        return jsonify({"error": "Empty message"}), 400
    
    try:
        reply = run_agent(user_input, conversation_history)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)

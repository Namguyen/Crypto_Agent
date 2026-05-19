import os
import requests
from openai import OpenAI
import dotenv

dotenv.load_dotenv()

# 1. Cấu hình DeepSeek API
# Nếu bạn dùng DeepSeek API chính hãng:
DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# Mẹo: Nếu bạn chạy DeepSeek offline/local qua Ollama trên máy, hãy đổi thành:
# client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")

# 2. Hàm lấy giá crypto từ CoinGecko (Cập nhật trong ngày)
def get_crypto_price(coin_name):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_name.lower()}&vs_currencies=usd,vnd"
    try:
        response = requests.get(url).json()
        if coin_name.lower() in response:
            data = response[coin_name.lower()]
            usd_price = data['usd']
            vnd_price = data['vnd']
            return f"💵 Giá của {coin_name.upper()} hôm nay:\n- USD: ${usd_price:,.2f}\n- VND: {vnd_price:,.0f} đ"
        else:
            return None
    except Exception:
        return "⚠️ Không thể kết nối đến API lấy giá lúc này."

# 3. Hàm xử lý chatbot bằng DeepSeek
def ask_deepseek(prompt):
    try:
        response = client.chat.completions.create(
            model="deepseek-chat", # Hoặc tên model local của bạn ví dụ: "deepseek-r1:7b" nếu dùng Ollama
            messages=[
                {"role": "system", "content": "Bạn là một trợ lý ảo chuyên về tiền điện tử (Crypto). Hãy trả lời ngắn gọn, dễ hiểu và thân thiện."},
                {"role": "user", "content": prompt}
            ],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ Lỗi kết nối DeepSeek: {e}"

# 4. Vòng lặp chính vận hành Bot
print("🤖 Trợ lý Crypto (DeepSeek) đã sẵn sàng! (Gõ 'exit' để thoát)")
print("-" * 40)

popular_coins = ['bitcoin', 'ethereum', 'solana', 'binancecoin', 'ripple', 'cardano']

while True:
    user_input = input("👶 Bạn: ").strip()
    if user_input.lower() == 'exit':
        print("🤖 Tạm biệt!")
        break
        
    if not user_input:
        continue

    # Kiểm tra xem người dùng có đang hỏi giá không
    is_asking_price = False
    for coin in popular_coins:
        if coin in user_input.lower() or (coin == 'bitcoin' and 'btc' in user_input.lower()) or (coin == 'ethereum' and 'eth' in user_input.lower()):
            print("🤖 Trợ lý: Đang check giá hôm nay...")
            price_info = get_crypto_price(coin)
            if price_info:
                print(price_info)
                is_asking_price = True
                break
    
    # Nếu không hỏi giá, dùng DeepSeek trả lời kiến thức
    if not is_asking_price:
        print("🤖 Trợ lý: DeepSeek đang suy nghĩ...")
        ai_response = ask_deepseek(user_input)
        print(f"🤖 Trợ lý:\n{ai_response}")
        
    print("-" * 40)
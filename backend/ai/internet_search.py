import os
from tavily import TavilyClient

def search_crypto_news(query: str) -> str:
    """Tìm kiếm tin tức và thông tin mới nhất về crypto bằng Tavily API."""
    # Khởi tạo Tavily Client (nên đặt ở ngoài để tối ưu)
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Lỗi: Chưa cấu hình TAVILY_API_KEY trong file .env"
    
    client = TavilyClient(api_key=api_key)
    
    # Thêm từ khóa "crypto" vào câu query để lọc kết quả
    search_query = f"crypto {query}"
    
    try:
        # Gọi API tìm kiếm, giới hạn 5 kết quả và lấy nội dung đầy đủ
        response = client.search(
            query=search_query,
            search_depth="advanced", # Lấy nội dung chi tiết từ trang web
            max_results=5,
            include_answer=True,    # Lấy câu trả lời tóm tắt nếu có
            include_raw_content=True, # Lấy nội dung thô
            topic="general"            # Ưu tiên tin tức
        )
        
        # Xử lý kết quả trả về
        answer = response.get('answer', '')
        results = response.get('results', [])
        
        if not results and not answer:
            return f"Rất tiếc, tôi không tìm thấy tin tức mới nhất về '{query}'."
        
        output = f"📰 **Kết quả tìm kiếm tin tức về '{query}'**\n\n"
        
        if answer:
            output += f"📌 **Tóm tắt:** {answer}\n\n"
        
        output += "🔍 **Các bài viết liên quan:**\n"
        for i, item in enumerate(results, 1):
            title = item.get('title', 'Không có tiêu đề')
            url = item.get('url', '#')
            content = item.get('content', '')[:200]  # Lấy 200 ký tự đầu
            output += f"{i}. **{title}**\n   📎 {url}\n   📄 {content}...\n\n"
        
        return output
    except Exception as e:
        return f"Lỗi khi kết nối tới Tavily API: {e}"


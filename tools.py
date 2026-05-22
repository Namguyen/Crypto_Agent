import requests
from tracker import keep_track, show_history

# Tool functions — actual logic
def get_crypto_price(coin_name: str) -> str:
    """Fetch live price from CoinGecko and log it."""
    url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={coin_name.lower()}&vs_currencies=usd,vnd"
    )
    try:
        data = requests.get(url, timeout=8).json()
        if coin_name.lower() not in data:
            return f" Không tìm thấy coin '{coin_name}' trên CoinGecko."
        usd = data[coin_name.lower()]["usd"]
        vnd = data[coin_name.lower()]["vnd"]
        keep_track(coin_name, usd, vnd)
        return (
            f"Giá {coin_name.upper()} hôm nay:\n"
            f"  - USD: ${usd:,.2f}\n"
            f"  - VND: {vnd:,.0f} đ"
        )
    except Exception as e:
        return f"Lỗi kết nối CoinGecko: {e}"


def get_price_history(coin: str = "", limit: int = 10) -> str:
    """Return recent price history, optionally filtered by coin."""
    return show_history(coin or None, limit)


# Tool schemas — what the LLM sees
TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_crypto_price",
            "description": (
                "Get the current real-time price of a cryptocurrency in USD and VND. "
                "Use when the user asks about price, value, or rate of any coin."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "coin_name": {
                        "type": "string",
                        "description": "CoinGecko coin id e.g. 'bitcoin', 'ethereum', 'solana'",
                    }
                },
                "required": ["coin_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": (
                "Retrieve past price lookups from local history. "
                "Use when user asks about previous prices, trends, or wants to compare over time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {
                        "type": "string",
                        "description": "Filter by coin name. Leave empty for all coins.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent records to return. Default 10.",
                    },
                },
                "required": [],
            },
        },
    },
]

# Tool map
TOOL_MAP = {
    "get_crypto_price":  get_crypto_price,
    "get_price_history": get_price_history,
}
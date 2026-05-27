import requests
from backend.ai.alert import add_alert, list_alerts, remove_alert, reset_alert
from backend.ai.internet_search import search_crypto_news
from backend.ai.price_history import keep_track, show_history
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
            return f"Coin '{coin_name}' not found on CoinGecko."
        usd = data[coin_name.lower()]["usd"]
        vnd = data[coin_name.lower()]["vnd"]
        keep_track(coin_name, usd, vnd)
        return (
            f"Price for {coin_name.upper()} today:\n"
            f"  - USD: ${usd:,.2f}\n"
            f"  - VND: {vnd:,.0f} VND"
        )
    except Exception as e:
        return f"CoinGecko connection error: {e}"


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
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_alert",
            "description": "Set a price alert for a cryptocurrency. The agent will monitor automatically and notify when price crosses the threshold.",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {
                        "type": "string",
                        "description": "CoinGecko coin id (e.g., 'bitcoin', 'ethereum', 'solana', 'ripple')"
                    },
                    "condition": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "Alert when price goes above or below the threshold"
                    },
                    "price": {
                        "type": "number",
                        "description": "Threshold price in USD"
                    }
                },
                "required": ["coin", "condition", "price"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_alert",
            "description": "Remove an existing alert using its ID (shown in list_alerts).",
            "parameters": {
                "type": "object",
                "properties": {
                    "alert_id": {
                        "type": "string",
                        "description": "The unique ID of the alert to remove"
                    }
                },
                "required": ["alert_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_alerts",
            "description": "Show all active price alerts (both pending and already triggered).",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reset_alert",
            "description": "Reset a triggered alert so it can fire again. Provide either alert_id or coin name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alert_id": {"type": "string", "description": "Alert ID to reset"},
                    "coin": {"type": "string", "description": "Coin name to reset all its triggered alerts"}
                }
            }
        }
    },
{
    "type": "function",
    "function": {
        "name": "search_crypto_news",
        "description": "Search the latest news and updates about the cryptocurrency market using Tavily. Use when the user asks about news, events, trends, or timely information related to crypto.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The query or keywords for crypto news the user wants to search. Example: 'bitcoin price today', 'news about Ethereum', 'latest DeFi trends'",
                }
            },
            "required": ["query"],
        },
    },
},
]

# Tool map
TOOL_MAP = {
    "get_crypto_price":  get_crypto_price,
    "get_price_history": get_price_history,
    "add_alert": add_alert,
    "remove_alert": remove_alert,
    "list_alerts": list_alerts,
    "reset_alert": reset_alert,
    "search_crypto_news": search_crypto_news,
}

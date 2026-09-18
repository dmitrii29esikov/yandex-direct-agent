from mcp.server.fastmcp import FastMCP
from api_client import YandexDirectAPIClient

# Создаём MCP-сервер
mcp = FastMCP("YandexDirectPro")

# Инициализируем API-клиент
api_client = YandexDirectAPIClient()

# Импорт инструментов (регистрируются автоматически)
from tools import campaigns, metrica

if __name__ == "__main__":
    mcp.run()
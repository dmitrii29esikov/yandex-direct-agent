import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

class YandexDirectAPIClient:
    def __init__(self):
        self.token = os.getenv("YANDEX_DIRECT_TOKEN")
        if not self.token:
            raise ValueError("YANDEX_DIRECT_TOKEN не найден в .env")
        self.base_url = "https://api.direct.yandex.com/json/v5"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8"
        }

    def post(self, service: str, method: str, params: dict, max_retries: int = 3):
        """Отправляет запрос к API Яндекс.Директа с автоматическим retry."""
        url = f"{self.base_url}/{service}"
        body = {"method": method, "params": params}
        
        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=body, headers=self.headers, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                # Обработка ошибок API
                if "error" in data:
                    error = data["error"]
                    # Ошибка 152 - нехватка баллов, ждём и повторяем
                    if error.get("error_code") == 152:
                        wait = error.get("error_detail", "60")
                        print(f"Лимит баллов, ждём {wait} сек...")
                        time.sleep(int(wait) + 5)
                        continue
                    return {"error": error}
                
                return data
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {"error": str(e)}
        
        return {"error": "Max retries exceeded"}

    def check_connection(self):
        """Проверяет подключение к API."""
        result = self.post("campaigns", "get", {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "Name"]
        })
        return result
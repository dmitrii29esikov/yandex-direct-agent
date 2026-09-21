import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

DIRECT_HOST = "api.direct.yandex.com"
DIRECT_SANDBOX_HOST = "api-sandbox.direct.yandex.com"

# Rasshifrovka kodov oshibok Direct API, kotorye my real'no videli v probah.
# Bez etogo agent poluchaet "HTTP 200 + error_code 8800" i ne ponimaet, chto delat'.
DIRECT_ERROR_HINTS = {
    53: "Нет доступа к этому аккаунту у текущего токена (проверьте права представителя)",
    54: "У токена нет прав для работы с агентскими аккаунтами — нельзя вести клиента через Client-Login",
    58: "Недостаточно прав для выполнения запроса к этому аккаунту",
    152: "Нехватка баллов — запрос нужно повторить позже",
    8800: "Неверный Client-Login: логин не существует или передан неверно",
}


def humanize_direct_error(error: dict) -> str:
    """Prevratim kod oshibki Direct v ponyatnyj tekst dlya agenta."""
    if not isinstance(error, dict):
        return str(error)
    code = error.get("error_code") or error.get("ErrorCode")
    detail = error.get("error_detail") or error.get("error_string") or ""
    hint = DIRECT_ERROR_HINTS.get(code) if isinstance(code, int) else None
    parts = [f"error_code {code}"] if code is not None else []
    if detail:
        parts.append(str(detail))
    if hint:
        parts.append(f"→ {hint}")
    return " | ".join(parts) if parts else str(error)


class YandexDirectAPIClient:
    """
    Klient Direct API v5.

    Rezhimy dostupa (gibridnaya shema):
    - token  : sobstvennyj token akkaunta, Client-Login NE peredaetsya;
    - agency : odin agentskij token + zagolovok Client-Login s loginom klienta,
               a takzhe Use-Operator-Units: true — tratim svoi baly, ne klienta.

    Obratnaya sovmestimost': YandexDirectAPIClient() bez argumentov rabotaet
    tak zhe, kak do pravok (token iz .env, bez Client-Login).
    """

    def __init__(
        self,
        token: str | None = None,
        client_login: str | None = None,
        use_operator_units: bool = False,
        sandbox: bool = False,
    ):
        self.token = token or os.getenv("YANDEX_DIRECT_TOKEN")
        if not self.token:
            raise ValueError("YANDEX_DIRECT_TOKEN не найден в .env")

        self.client_login = client_login
        self.use_operator_units = bool(use_operator_units)
        self.sandbox = bool(sandbox)

        host = DIRECT_SANDBOX_HOST if self.sandbox else DIRECT_HOST
        self.base_url = f"https://{host}/json/v5"

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.client_login:
            self.headers["Client-Login"] = self.client_login
        if self.use_operator_units:
            self.headers["Use-Operator-Units"] = "true"

        # Nablyudaemost': poslednie znacheniya iz zagolovkov otveta.
        self.last_units = None          # "potracheno/ostatok/limit"
        self.last_request_id = None

    # ---------- nablyudaemost' ----------

    def _capture_headers(self, response):
        """Zapominaem Units i RequestId — bez nih podderzhka Yandex rabotaet vslepuyu."""
        self.last_units = response.headers.get("Units") or self.last_units
        self.last_request_id = response.headers.get("RequestId") or self.last_request_id

    def units_info(self) -> dict | None:
        """Razbiraem 'Units: potracheno/ostatok/limit' v chisla."""
        if not self.last_units:
            return None
        try:
            spent, rest, limit = (int(x) for x in self.last_units.split("/"))
        except (ValueError, AttributeError):
            return {"raw": self.last_units}
        pct = round(rest / limit * 100, 1) if limit else None
        return {
            "spent": spent, "remaining": rest, "limit": limit,
            "remaining_percent": pct, "warning": bool(pct is not None and pct < 20),
        }

    # ---------- osnovnoj zapros ----------

    def post(self, service: str, method: str, params: dict, max_retries: int = 3):
        """Otpravlyaet zapros k API Yandex.Direkta s avtomaticheskim retry."""
        url = f"{self.base_url}/{service}"
        body = {"method": method, "params": params}

        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=body, headers=self.headers, timeout=30)
                self._capture_headers(response)
                response.raise_for_status()
                data = response.json()

                # Obrabotka oshibok API
                if "error" in data:
                    error = data["error"]
                    # Oshibka 152 - nehvatka ballov, zhdem i povtoryaem
                    if error.get("error_code") == 152:
                        wait = error.get("error_detail", "60")
                        print(f"Лимит баллов, ждём {wait} сек...")
                        time.sleep(int(wait) + 5)
                        continue
                    return {
                        "error": error,
                        "error_text": humanize_direct_error(error),
                        "request_id": self.last_request_id,
                    }

                return data
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return {"error": str(e)}

        return {"error": "Max retries exceeded"}

    def check_connection(self):
        """Proveryaet podklyuchenie k API."""
        return self.post("campaigns", "get", {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "Name"],
        })

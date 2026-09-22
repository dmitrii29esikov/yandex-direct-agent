# -*- coding: utf-8 -*-
"""
Матрица доступов: какой токен что умеет.

Зачем: заявка на доступ к API Директа — свойство ПРИЛОЖЕНИЯ. Пока она не
одобрена, токен этого приложения Директ не видит (ошибка 58). Этот скрипт
показывает состояние по каждому токену, чтобы после одобрения заявки было
видно результат без догадок.

Запуск (из папки проекта):
    python apps_check.py

Токены не печатаются — только маска.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv

import accounts_store as store

load_dotenv()

DIRECT_URL = "https://api.direct.yandex.com/json/v5/campaigns"
METRICA_URL = "https://api-metrika.yandex.net/management/v1/counters?limit=1"
YTM_URL = "https://api.ytm.yandex.net/ytm/management/v1/container/1007795"


def _short(text, limit=64):
    return " ".join(str(text).split())[:limit]


def check_direct(token) -> str:
    try:
        r = requests.post(
            DIRECT_URL,
            headers={"Authorization": f"Bearer {token}", "Accept-Language": "ru",
                     "Content-Type": "application/json; charset=utf-8"},
            json={"method": "get", "params": {"SelectionCriteria": {}, "FieldNames": ["Id"]}},
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        return f"сеть: {type(e).__name__}"
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if "error" in body:
        err = body["error"]
        return f"[{err.get('error_code')}] {_short(err.get('error_string') or err.get('error_detail'))}"
    camps = ((body.get("result") or {}).get("Campaigns")) or []
    return f"OK, кампаний {len(camps)}"


def check_metrica(token) -> str:
    try:
        r = requests.get(METRICA_URL, headers={"Authorization": f"OAuth {token}"}, timeout=30)
    except requests.exceptions.RequestException as e:
        return f"сеть: {type(e).__name__}"
    if r.status_code == 200:
        data = r.json()
        return f"OK, счётчиков {data.get('rows')}"
    return f"[{r.status_code}] {_short(r.content.decode('utf-8', 'replace'))}"


def check_ytm(token) -> str:
    try:
        r = requests.get(YTM_URL, headers={"Authorization": f"OAuth {token}"}, timeout=30)
    except requests.exceptions.RequestException as e:
        return f"сеть: {type(e).__name__}"
    if r.status_code == 200:
        body = r.json()
        if "error" in body:
            return f"[{body['error'].get('error_code')}] {_short(body['error'].get('error_string'))}"
        return "OK, контейнер доступен"
    return f"[{r.status_code}] {_short(r.content.decode('utf-8', 'replace'))}"


def collect_tokens() -> list:
    """Токены из .env и из secrets/secrets.json."""
    out = []
    for name in ("YANDEX_DIRECT_TOKEN", "YANDEX_METRICA_TOKEN", "YANDEX_YTM_TOKEN"):
        token = os.getenv(name)
        if token:
            out.append((name, token))

    secrets_path = store.SECRETS_FILE
    if secrets_path.exists():
        import json
        data = json.loads(secrets_path.read_text(encoding="utf-8"))
        for account, bucket in data.items():
            for contour, token in (bucket or {}).items():
                if token and all(token != t for _, t in out):
                    out.append((f"{account}.{contour}", token))
    return out


def main():
    tokens = collect_tokens()
    if not tokens:
        print("Токенов не найдено")
        return

    print("Матрица доступов (проверка только на чтение)\n")
    header = f"{'ТОКЕН':<22} {'МАСКА':<16} {'ДИРЕКТ':<34} {'МЕТРИКА':<22} {'YTM'}"
    print(header)
    print("-" * len(header))

    for label, token in tokens:
        row = [
            label[:21].ljust(22),
            store.mask(token).ljust(16),
            check_direct(token)[:33].ljust(34),
            check_metrica(token)[:21].ljust(22),
            check_ytm(token)[:30],
        ]
        print(" ".join(row))

    print()
    print("Как читать: если у токена в колонке ДИРЕКТ стоит ошибка 58 —")
    print("приложению нужна одобренная заявка на доступ к API.")
    print("Одобренное приложение обслуживает неограниченное число логинов.")


if __name__ == "__main__":
    main()

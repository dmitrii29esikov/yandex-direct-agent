"""
Yandex Tag Manager: номер контейнера по счётчику Метрики.

Зачем этот модуль. У API Тег Менеджера НЕТ метода со списком контейнеров —
проверено: все варианты (`/containers`, `/container`, `/containers/list`,
`/user/containers`) отдают 404. В объекте счётчика Метрики ссылки на контейнер
тоже нет — проверил все поля с `cont`/`ytm`/`tag` в названии.

Значит, программно узнать номер контейнера можно только одним способом:
публичным endpoint, который загружает тег счётчика:

    GET https://mc.yandex.ru/ytm-config/<counter_id>
    -> {"containerVersion": "114", "containerId": 1007795, "triggers": [...], ...}

Токен для этого не нужен: endpoint публичный, как и сам код счётчика. Но чтобы
читать теги и триггеры через API Тег Менеджера, наш YTM-токен должен иметь
доступ именно к этому контейнеру — это проверяется отдельно.

Бонус: в ответе лежит скомпилированная конфигурация (теги, триггеры,
переменные, права). То есть видно, что реально развёрнуто на сайте, — это
ценно для аудита разметки.
"""

import logging

import requests

log = logging.getLogger("ytm.config")

YTM_CONFIG_URL = "https://mc.yandex.ru/ytm-config/{counter_id}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YandexDirectAgent)"}


def fetch_config(counter_id: int, timeout: int = 20) -> dict | None:
    """
    Возвращает конфигурацию контейнера, привязанного к счётчику.

    None — если счётчика нет, к нему не привязан контейнер или сеть недоступна.
    """
    try:
        response = requests.get(YTM_CONFIG_URL.format(counter_id=counter_id),
                                headers=HEADERS, timeout=timeout)
    except requests.exceptions.RequestException as e:
        log.warning("ytm-config/%s недоступен: %s", counter_id, e)
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except ValueError:
        # Счётчик без контейнера отдаёт не JSON, а пустой ответ или HTML.
        return None

    return data if isinstance(data, dict) else None


def container_id(counter_id: int) -> int | None:
    """Номер контейнера Тег Менеджера для счётчика Метрики (или None)."""
    config = fetch_config(counter_id)
    if not config:
        return None
    value = config.get("containerId")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def summary(counter_id: int) -> dict:
    """Короткая сводка по контейнеру счётчика — для отчётов и инструментов."""
    config = fetch_config(counter_id)
    if not config:
        return {"counter_id": counter_id, "ytm": False}

    return {
        "counter_id": counter_id,
        "ytm": True,
        "container_id": config.get("containerId"),
        "container_version": config.get("containerVersion"),
        "compiler_version": config.get("compilerVersion"),
        "tags": len(config.get("tags") or []),
        "triggers": len(config.get("triggers") or []),
        "variables": len(config.get("variables") or []),
    }

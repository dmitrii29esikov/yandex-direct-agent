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
    """
    Короткая сводка по контейнеру счётчика — для отчётов и инструментов.

    Два поля требуют пояснения:

    `in_use` — добавлены ли в контейнер теги или триггеры. Контейнер может быть
    включён на счётчике, но оставаться пустым: тогда он ничего не собирает,
    и это не ошибка разметки, а просто незаконченная настройка.

    `placeholder_id` — номер контейнера подозрительно мал. Реальные номера
    четырёх-семизначные (1007795, 142940, 872525). Если пришло что-то вроде 1,
    значит контейнер ещё не создан, а в конфиг подставлена заглушка.
    """
    config = fetch_config(counter_id)
    if not config:
        return {"counter_id": counter_id, "ytm": False, "in_use": False}

    tags = config.get("tags") or []
    triggers = config.get("triggers") or []
    variables = config.get("variables") or []

    raw_container = config.get("containerId")
    try:
        container = int(raw_container) if raw_container is not None else None
    except (TypeError, ValueError):
        container = None

    return {
        "counter_id": counter_id,
        "ytm": True,
        "container_id": container,
        "container_version": config.get("containerVersion"),
        "compiler_version": config.get("compilerVersion"),
        "tags": len(tags),
        "triggers": len(triggers),
        "variables": len(variables),
        "in_use": bool(tags or triggers),
        "placeholder_id": bool(container is not None and container < 1000),
    }

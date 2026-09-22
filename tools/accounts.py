"""
Upravlenie akkauntami dlya MCP-agenta.

Zdes' zhivut instrumenty onbordinga: posmotret' reestr, proverit' dostupy,
avtomaticheski najti vse, chto nam uzhe vydali, i podklyuchit' novyj akkaunt.

Vazhno: tokeny nikogda ne vozvraschaem v otvet — tol'ko masku (y0__...f3a2).
"""

import logging
import os

from mcp_instance import mcp

import access
import accounts_store as store
import ytm_config

log = logging.getLogger("tools.accounts")

MASK = store.mask


# --------------------------------------------------------------------------
# 1. Reestr i diagnostika
# --------------------------------------------------------------------------

@mcp.tool()
def list_accounts() -> dict:
    """Список аккаунтов в реестре: режим доступа, счётчики, контейнеры, токены (замаскированы)."""
    try:
        ids = store.account_ids()
    except Exception as e:
        return {"error": f"Реестр недоступен: {e}"}

    out = []
    for name in ids:
        try:
            out.append(access.describe(name))
        except access.AccessError as e:
            out.append({"account": name, "error": str(e)})
    return {"count": len(out), "default": store.DEFAULT_ACCOUNT, "accounts": out}


@mcp.tool()
def check_account(account: str | None = None) -> dict:
    """
    Проверка доступов аккаунта по трём контурам: Директ, Метрика, Tag Manager.

    Возвращает по каждому контуру: ok / ошибку с понятным диагнозом.
    Именно этот инструмент показывает, кто из клиентов «отвалился».
    """
    try:
        name, _ = access.context(account)
    except access.AccessError as e:
        return {"error": str(e)}

    result = {"account": name, "contours": {}}

    # --- Direct
    try:
        client = access.direct(name)
        params = {"SelectionCriteria": {}, "FieldNames": ["Id", "Name", "State", "Status"]}
        res = client.post("campaigns", "get", params)
        if isinstance(res, dict) and res.get("error"):
            result["contours"]["direct"] = {
                "ok": False,
                "mode": access.direct_mode(name),
                "error": res.get("error_text") or res.get("error"),
            }
        else:
            campaigns = (res.get("result") or {}).get("Campaigns", [])
            result["contours"]["direct"] = {
                "ok": True,
                "mode": access.direct_mode(name),
                "client_login": client.client_login,
                "campaigns": len(campaigns),
                "units": client.units_info(),
            }
    except access.AccessError as e:
        result["contours"]["direct"] = {"ok": False, "error": str(e)}

    # --- Metrika
    counters = access.metrica_get(name, "/management/v1/counters")
    if isinstance(counters, dict) and counters.get("error"):
        result["contours"]["metrica"] = {"ok": False, "error": counters["error"]}
    else:
        items = (counters or {}).get("counters", [])
        # Ostavlyaem tolko nashi schetchiki, esli oni ukazany v reestre
        linked = access.metrica_counter_ids(name)
        if linked:
            items = [c for c in items if c.get("id") in linked]
        result["contours"]["metrica"] = {
            "ok": True,
            "ulogin": access.metrica_ulogin(name),
            "counters": [
                {"id": c.get("id"), "name": c.get("name"),
                 "owner_login": c.get("owner_login"), "status": c.get("status"),
                 "activity_status": c.get("activity_status")}
                for c in items
            ],
        }

    # --- Tag Manager
    containers = access.ytm_container_ids(name)
    if not containers:
        result["contours"]["ytm"] = {"ok": False, "error": "Контейнеры не указаны в реестре"}
    else:
        ytm_checks = []
        for cid in containers:
            data = access.ytm_get(name, f"container/{cid}")
            if isinstance(data, dict) and data.get("error"):
                ytm_checks.append({"container_id": cid, "ok": False, "error": data["error"]})
            else:
                cont = (data or {}).get("container", {})
                ytm_checks.append({
                    "container_id": cid, "ok": True,
                    "counter_id": cont.get("counter_id"),
                    "note": "API Tag Manager — только чтение",
                })
        result["contours"]["ytm"] = {"ok": all(c["ok"] for c in ytm_checks), "containers": ytm_checks}

    result["ok"] = all(c.get("ok") for c in result["contours"].values())
    return result


@mcp.tool()
def describe_account(account: str | None = None) -> dict:
    """Паспорт аккаунта: режим, логины, привязки, цели. Токены — только маской."""
    try:
        return access.describe(account)
    except access.AccessError as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------
# 2. Avtoobnaruzhenie: chto nam uzhe vydali
# --------------------------------------------------------------------------

@mcp.tool()
def discover_accounts(account: str | None = None, save: bool = False) -> dict:
    """
    Автопоиск всего, что нам уже выдали.

    - Метрика: список счётчиков, сгруппированный по владельцу (owner_login).
    - Директ: для каждого владельца — /management/v1/clients?ulogin=...
      возвращает клиентов Директа и их chief_login, то есть логин для Client-Login.

    save=True — записать найденные привязки в реестр (аккаунты не удаляются).
    """
    try:
        name, _ = access.context(account)
    except access.AccessError as e:
        return {"error": str(e)}

    counters = access.metrica_get(name, "/management/v1/counters")
    if isinstance(counters, dict) and counters.get("error"):
        return {"error": counters["error"], "stage": "metrica/counters"}

    owners: dict = {}
    for c in (counters or {}).get("counters", []):
        owner = c.get("owner_login") or "unknown"
        owners.setdefault(owner, []).append({
            "id": c.get("id"), "name": c.get("name"), "site": c.get("site"),
            "status": c.get("status"), "activity_status": c.get("activity_status"),
        })

    findings = []
    for owner, items in owners.items():
        entry = {"owner_login": owner, "counters": items, "direct_clients": [], "direct_error": None}
        counter_ids = [c["id"] for c in items if c.get("id")]
        # Metrika /clients trebuet parametr counters — bez nego 400.
        clients = access.metrica_get(
            name, "/management/v1/clients",
            {"ulogin": owner, "counters": ",".join(str(c) for c in counter_ids)})
        if isinstance(clients, dict) and clients.get("error"):
            entry["direct_error"] = clients["error"]
        else:
            entry["direct_clients"] = (clients or {}).get("clients", [])
        findings.append(entry)

    saved = []
    if save:
        for entry in findings:
            slug = _slug(entry["owner_login"])
            if store.resolve(slug) is None and slug not in store.all_accounts():
                chief = (entry["direct_clients"] or [{}])[0].get("chief_login")
                record = {
                    "title": entry["owner_login"],
                    "direct": {"mode": "token", "client_login": chief},
                    "metrica": {
                        "counter_ids": [c["id"] for c in entry["counters"]],
                        "ulogin": entry["owner_login"],
                    },
                    "ytm": {"container_ids": []},
                    "goals": {},
                }
                store.upsert_account(slug, record)
                saved.append(slug)
            else:
                store.link_counters(_slug(entry["owner_login"]),
                                    [c["id"] for c in entry["counters"]],
                                    ulogin=entry["owner_login"])

    return {
        "account": name,
        "owners_found": len(findings),
        "findings": findings,
        "saved_accounts": saved,
        "note": "Токены не обнаружены автоматически — их нужно добавить через set_account_tokens",
    }


def _slug(login: str) -> str:
    return str(login or "unknown").lower().replace("-", "").replace(".", "").replace("_", "")


# --------------------------------------------------------------------------
# 3. Podklyuchenie novogo akkaunta
# --------------------------------------------------------------------------

@mcp.tool()
def add_account(
    account: str,
    title: str | None = None,
    mode: str = "token",
    client_login: str | None = None,
    client_id: int | None = None,
    direct_token: str | None = None,
    metrica_token: str | None = None,
    ytm_token: str | None = None,
    counter_ids: list[int] | None = None,
    container_ids: list[int] | None = None,
    ulogin: str | None = None,
) -> dict:
    """
    Подключить новый аккаунт.

    mode='token'  — свой токен аккаунта (владелец выдал доступ нам как представителю);
    mode='agency' — один агентский токен + client_login (Client-Login), баллы наши.

    Токены сохраняются в secrets/secrets.json (в git не попадают) и в ответе не возвращаются.
    """
    mode = (mode or "token").lower()
    if mode not in ("token", "agency"):
        return {"error": "mode должен быть 'token' или 'agency'"}

    record = {
        "title": title or account,
        "direct": {"mode": mode, "client_login": client_login, "client_id": client_id},
        "metrica": {"counter_ids": sorted({int(c) for c in (counter_ids or [])}), "ulogin": ulogin or client_login},
        "ytm": {"container_ids": sorted({int(c) for c in (container_ids or [])})},
        "goals": {},
    }
    if mode == "token":
        record["direct"]["token_ref"] = account

    store.upsert_account(account, record, tokens={
        "direct": direct_token, "metrica": metrica_token, "ytm": ytm_token,
    })
    return {
        "ok": True,
        "account": account,
        "mode": mode,
        "saved_tokens": {
            "direct": MASK(direct_token),
            "metrica": MASK(metrica_token),
            "ytm": MASK(ytm_token),
        },
        "next_step": f"check_account(account='{account}')",
    }


@mcp.tool()
def set_account_tokens(
    account: str,
    direct_token: str | None = None,
    metrica_token: str | None = None,
    ytm_token: str | None = None,
) -> dict:
    """Обновить токены аккаунта (например, если клиент перевыпустил доступ)."""
    name = store.resolve(account) or account
    if name not in store.all_accounts():
        return {"error": f"Аккаунт '{name}' не найден. Сначала add_account()."}
    saved = store.set_tokens(name, {
        "direct": direct_token, "metrica": metrica_token, "ytm": ytm_token,
    })
    return {"ok": True, "account": name, "tokens_present": saved}


@mcp.tool()
def link_account_data(
    account: str,
    counter_ids: list[int] | None = None,
    container_ids: list[int] | None = None,
    ulogin: str | None = None,
    client_login: str | None = None,
) -> dict:
    """Привязать к аккаунту счётчики Метрики и контейнеры Tag Manager."""
    name = store.resolve(account) or account
    if name not in store.all_accounts():
        return {"error": f"Аккаунт '{name}' не найден. Сначала add_account()."}

    if counter_ids:
        store.link_counters(name, counter_ids, ulogin=ulogin)
    if container_ids:
        store.link_containers(name, container_ids)

    if client_login or ulogin:
        accounts = store.all_accounts()
        acc = accounts[name]
        if client_login:
            acc.setdefault("direct", {})["client_login"] = client_login
        if ulogin:
            acc.setdefault("metrica", {})["ulogin"] = ulogin
        store.upsert_account(name, acc)

    return {"ok": True, "account": name, "described": access.describe(name)}


@mcp.tool()
def oauth_link() -> dict:
    """
    Ссылка для владельца аккаунта: он жмёт «Разрешить» и присылает токен.

    Требуется YANDEX_OAUTH_CLIENT_ID в .env — id нашего приложения на oauth.yandex.ru
    со скоупами direct:api, metrika:read, metrika:write, ytm:read.
    """
    client_id = os.getenv("YANDEX_OAUTH_CLIENT_ID")
    if not client_id:
        return {
            "error": "YANDEX_OAUTH_CLIENT_ID не задан в .env",
            "how_to": [
                "1. Создайте приложение на https://oauth.yandex.ru/client/new",
                "2. Платформа — веб-сервисы, redirect URI — https://oauth.yandex.ru/verification_code",
                "3. Доступ к данным — direct:api, metrika:read, metrika:write, ytm:read",
                "4. Скопируйте ClientID в .env как YANDEX_OAUTH_CLIENT_ID",
            ],
        }
    return {
        "url": f"https://oauth.yandex.ru/authorize?response_type=token&client_id={client_id}",
        "instructions": [
            "Отправьте ссылку владельцу аккаунта.",
            "Он входит под нужным логином и нажимает «Разрешить».",
            "Копирует token из адресной строки и присылает вам.",
            f"Затем: add_account(account='<имя>', mode='token', direct_token=<токен>, ...)",
        ],
        "scope_note": "Один токен с этим набором скоупов подходит и для Директа, и для Метрики, и для YTM.",
    }


@mcp.tool()
def discover_containers(account: str | None = None, save: bool = True) -> dict:
    """
    Находит контейнеры Яндекс Тег Менеджера для счётчиков аккаунта.

    Почему это отдельный инструмент: у API Тег Менеджера НЕТ метода со списком
    контейнеров (проверено — все варианты отдают 404), а в объекте счётчика
    Метрики нет ссылки на контейнер. Единственный программный путь — публичный
    endpoint ytm-config, который загружает тег счётчика на сайте.

    Привязка идемпотентна: счётчик и контейнер принадлежат ровно одному
    аккаунту, чужие привязки не перехватываются.

    save=True — привязывает найденные доступные контейнеры к аккаунту.
    """
    try:
        name, record = access.context(account)
    except access.AccessError as e:
        return {"error": str(e)}

    # Привязки, уже занятые другими аккаунтами: не перехватываем их.
    foreign_counters, foreign_containers = set(), set()
    for other, rec in store.all_accounts().items():
        if other == name:
            continue
        foreign_counters |= {int(c) for c in
                             ((rec.get("metrica") or {}).get("counter_ids") or [])}
        foreign_containers |= {int(c) for c in
                               ((rec.get("ytm") or {}).get("container_ids") or [])}

    counter_ids = list(access.metrica_counter_ids(name))
    counters_meta = {}

    data = access.metrica_get(name, "/management/v1/counters")
    if isinstance(data, dict) and not data.get("error"):
        for c in data.get("counters", []):
            counters_meta[c.get("id")] = c
        if not counter_ids:
            counter_ids = [c.get("id") for c in data.get("counters", []) if c.get("id")]
    elif not counter_ids:
        return {"error": data.get("error") if isinstance(data, dict) else "нет данных"}

    found, not_accessible, without_ytm, skipped = [], [], [], []

    for counter_id in counter_ids:
        meta = counters_meta.get(counter_id) or {}
        base = {
            "counter_id": counter_id,
            "counter_name": meta.get("name"),
            "owner_login": meta.get("owner_login"),
            "site": meta.get("site"),
        }

        if counter_id in foreign_counters and not access.metrica_counter_ids(name):
            skipped.append({**base, "reason": "привязан к другому аккаунту"})
            continue

        info = ytm_config.summary(counter_id)
        if not info.get("ytm") or not info.get("container_id"):
            without_ytm.append(base)
            continue

        container_id = int(info["container_id"])
        in_use = bool(info.get("in_use"))

        probe = access.ytm_get(name, f"container/{container_id}")
        accessible = not (isinstance(probe, dict) and probe.get("error"))

        entry = {
            **base,
            "container_id": container_id,
            "container_version": info.get("container_version"),
            "tags_in_config": info.get("tags"),
            "triggers_in_config": info.get("triggers"),
            "in_use": in_use,
            "api_accessible": accessible,
        }

        if not accessible:
            # Разделяем два разных случая: контейнера ещё нет (заглушка, тегов
            # нет) и контейнер есть, но не выдан нашему токену.
            if info.get("placeholder_id") or not in_use:
                entry["status"] = "не создан или не используется"
                entry["hint"] = ("Контейнер ещё не создан: теги и триггеры не добавлены, "
                                 "разметка ничего не собирает. Это не ошибка — просто не настроено.")
            else:
                entry["status"] = "нет доступа у токена"
                entry["api_error"] = probe.get("error")
                entry["hint"] = "Контейнер есть, но не выдан нашему YTM-токену"
            not_accessible.append(entry)
            continue

        if container_id in foreign_containers:
            entry["hint"] = "Контейнер уже привязан к другому аккаунту"
            skipped.append(entry)
            continue

        if not in_use:
            entry["status"] = "создан, но пустой"
            entry["hint"] = "Контейнер есть, но теги не добавлены — данные не собираются"
        else:
            entry["status"] = "работает"

        found.append(entry)

    if save and found:
        store.link_containers(name, [f["container_id"] for f in found])

    return {
        "account": name,
        "counters_checked": len(counter_ids),
        "containers_found": len(found),
        "saved": bool(save and found),
        "found": found,
        "found_not_accessible": not_accessible,
        "without_ytm": without_ytm,
        "skipped_foreign": skipped,
        "note": "Контейнер читается через API только если выдан нашему "
                "YTM-токену. Привязка не перехватывает чужие счётчики.",
    }

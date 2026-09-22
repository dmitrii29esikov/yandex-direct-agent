"""
Sloj dostupa: edinstvennoe mesto, gde zhivet raznica mezhdu rezhimami.

    agency : odin agentskij token + Client-Login klienta + Use-Operator-Units
    token  : sobstvennyj token akkaunta (naprimer, vladelec dal nam dostup)

Instrumenty (tools/*) ne znayut ni pro tokeny, ni pro agentstvo: oni poluchayut
imya akkaunta i spashivayut u etogo modulya gotovyj provider.
"""

import logging
import os

import requests

import accounts_store as store
from api_client import YandexDirectAPIClient

log = logging.getLogger("access")

METRICA_BASE = "https://api-metrika.yandex.net"
YTM_BASE = "https://api.ytm.yandex.net/ytm/management/v1"


class AccessError(Exception):
    """Dostup k konturu ne nastroen ili otozvan. Tekst uzhe ponyaten cheloveku."""


def _sandbox() -> bool:
    return str(os.getenv("YANDEX_DIRECT_SANDBOX", "")).lower() in ("1", "true", "yes")


def context(account: str | None = None) -> tuple:
    """(imya akkaunta, ego zapis'). Lyuboj znakomyj ID razreshaetsya v imya."""
    resolved = store.resolve_or_default(account)
    try:
        return resolved, store.get(resolved)
    except KeyError:
        raise AccessError(
            f"Аккаунт '{resolved}' не найден в реестре. "
            f"Доступные: {', '.join(store.account_ids()) or '—'}"
        )


# --------------------------------------------------------------------------
# Direct
# --------------------------------------------------------------------------

def direct(account: str | None = None) -> YandexDirectAPIClient:
    """Gotovyj klient Direct API dlya akkaunta."""
    name, acc = context(account)
    d = acc.get("direct") or {}
    mode = (d.get("mode") or "token").lower()

    if mode == "agency":
        token = store.agency_token()
        if not token:
            raise AccessError(
                "Режим agency, но агентский токен не задан. "
                "Укажите YANDEX_AGENCY_TOKEN в .env или secrets['agency']['direct']."
            )
        return YandexDirectAPIClient(
            token=token,
            client_login=d.get("client_login"),
            use_operator_units=True,      # tratim svoi baly, a ne baly klienta
            sandbox=_sandbox(),
        )

    token = store.get_secret(name, acc, "direct")
    if not token:
        raise AccessError(
            f"Для аккаунта '{name}' не задан token Direct. "
            f"Вызовите set_account_tokens(account='{name}', direct_token=...) "
            f"или переключите аккаунт в режим agency."
        )
    # Client-Login — только для агентского доступа. В режиме token он не нужен
    # и вреден: токен уже принадлежит самому рекламодателю.
    return YandexDirectAPIClient(
        token=token,
        client_login=None,
        sandbox=_sandbox(),
    )


def direct_mode(account: str | None = None) -> str:
    _, acc = context(account)
    return (acc.get("direct") or {}).get("mode", "token")


# --------------------------------------------------------------------------
# Metrika
# --------------------------------------------------------------------------

def metrica_headers(account: str | None = None) -> dict:
    name, acc = context(account)
    token = store.get_secret(name, acc, "metrica")
    if not token:
        raise AccessError(
            f"Для аккаунта '{name}' не задан token Metrika. "
            f"Вызовите set_account_tokens(account='{name}', metrica_token=...)"
        )
    return {"Authorization": f"OAuth {token}", "Content-Type": "application/json"}


def metrica_ulogin(account: str | None = None) -> str | None:
    _, acc = context(account)
    return (acc.get("metrica") or {}).get("ulogin")


def metrica_get(account: str | None, path: str, params: dict | None = None,
                add_ulogin: bool = True, timeout: int = 30,
                retries: int = 1) -> dict:
    """
    GET k Metrika Management API s predstavitelskim dostupom.

    GET-parametr ulogin (sm. spravku Metriki) pozvolyaet rabotat' s akkauntami,
    k kotorym u nas predstavitelskij dostup.
    """
    try:
        headers = metrica_headers(account)
    except AccessError as e:
        return {"error": str(e)}

    params = dict(params or {})
    ulogin = metrica_ulogin(account)
    if add_ulogin and ulogin:
        params.setdefault("ulogin", ulogin)

    url = f"{METRICA_BASE}{path}"
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params,
                                timeout=timeout * (attempt + 1))
            break
        except requests.exceptions.RequestException as e:
            last_error = e
    else:
        return {"error": f"Сеть недоступна после {retries + 1} попыток: {last_error}"}

    if resp.status_code == 401:
        return {"error": "401: токен Метрики отозван или неверен — переподключите аккаунт"}
    if resp.status_code == 403:
        return {"error": "403: владелец счётчика не выдал представительский доступ"}
    if resp.status_code == 404:
        return {"error": f"404: не найдено — {path}"}
    if resp.status_code == 429:
        return {"error": "429: превышен лимит запросов Метрики"}
    if resp.status_code != 200:
        return {"error": f"Метрика {resp.status_code}: {resp.text[:300]}"}

    try:
        return resp.json()
    except ValueError:
        return {"error": "Метрика вернула не JSON", "raw": resp.text[:300]}


# --------------------------------------------------------------------------
# Yandex Tag Manager
# --------------------------------------------------------------------------

def ytm_headers(account: str | None = None) -> dict:
    name, acc = context(account)
    token = store.get_secret(name, acc, "ytm")
    if not token:
        raise AccessError(
            f"Для аккаунта '{name}' не задан token YTM. "
            f"Вызовите set_account_tokens(account='{name}', ytm_token=...)"
        )
    return {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def ytm_get(account: str | None, path: str, timeout: int = 30, retries: int = 1) -> dict:
    """
    GET k YTM API. API YTM tol'ko dlya chteniya — zapisi ne byvayut.

    U YTM byvayut medlennye otvety (osobenno /variables), poetomu pri tajmaute
    delaem odnu povtornuyu popytku s uvelichennym tajmautom vmesto togo,
    chtoby srazu pisat' ""ne provereno"".
    """
    try:
        headers = ytm_headers(account)
    except AccessError as e:
        return {"error": str(e)}

    url = f"{YTM_BASE}/{path}"
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout * (attempt + 1))
            break
        except requests.exceptions.RequestException as e:
            last_error = e
    else:
        return {"error": f"Сеть недоступна после {retries + 1} попыток: {last_error}"}

    if resp.status_code == 401:
        return {"error": "401: токен YTM отозван или без доступа 'ytm:read'"}
    if resp.status_code == 403:
        return {"error": "403: нет доступа к этому контейнеру"}
    if resp.status_code == 404:
        return {"error": f"404: не найдено — {path}"}
    if resp.status_code == 429:
        return {"error": "429: превышен лимит 5000 запросов в сутки"}
    if resp.status_code != 200:
        return {"error": f"YTM {resp.status_code}: {resp.text[:300]}"}

    try:
        return resp.json()
    except ValueError:
        return {"error": "YTM вернул не JSON", "raw": resp.text[:300]}


def priority_goals(account: str | None = None) -> dict:
    """
    Prioritetnye tseli po kampaniyam: {campaign_id: [goal_id, ...]}.

    Berem iz reestra. Zachem v reestre, a ne iz API: prоритетnye tseli zhivut
    vnutri inline-strategii kampanii, a eto pole cherez API ne chitaetsya
    (servis campaigns ne podderzhivaet TextCampaign, API v4 otklyuchen, servis
    strategies otdaet tol'ko pакетnye strategii). Znacheniya zapolnyayutsya
    odin raz iz interfejsa ili iz otcheta drugogo instrumenta.

    Bez nih analiz «den'gi v ploshchadki bez celevyh» schitaet konversii po
    vsem tselyam schetchika i daet druguyu kartinu.
    """
    name, record = context(account)
    raw = (record.get("direct") or {}).get("priority_goals") or {}
    result = {}
    for campaign_id, goals in raw.items():
        try:
            result[int(campaign_id)] = [int(g) for g in goals]
        except (TypeError, ValueError):
            continue
    return result


def ytm_container_ids(account: str | None = None) -> list:
    _, acc = context(account)
    return list((acc.get("ytm") or {}).get("container_ids") or [])


def metrica_counter_ids(account: str | None = None) -> list:
    _, acc = context(account)
    return list((acc.get("metrica") or {}).get("counter_ids") or [])


# --------------------------------------------------------------------------
# opisanie dlya diagnostiki (bez tokenov!)
# --------------------------------------------------------------------------

def describe(account: str | None = None) -> dict:
    name, acc = context(account)
    d = acc.get("direct") or {}
    info = {
        "account": name,
        "title": acc.get("title"),
        "person": acc.get("person"),
        "role": acc.get("role"),
        "aliases": acc.get("aliases") or [],
        "direct": {
            "mode": d.get("mode", "token"),
            "login": d.get("login"),
            "client_login": d.get("client_login"),
            "client_id": d.get("client_id"),
            "token": store.mask(
                store.agency_token() if (d.get("mode") == "agency") else store.get_secret(name, acc, "direct")
            ),
        },
        "metrica": {
            "counter_ids": metrica_counter_ids(name),
            "ulogin": metrica_ulogin(name),
            "token": store.mask(store.get_secret(name, acc, "metrica")),
        },
        "ytm": {
            "container_ids": ytm_container_ids(name),
            "token": store.mask(store.get_secret(name, acc, "ytm")),
        },
        "goals": acc.get("goals") or {},
    }
    return info

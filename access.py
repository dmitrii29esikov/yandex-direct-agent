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

MICROS = 1_000_000          # Direct otdaet dengi v mikroedinicah (1 000 000 = 1 ₽)

# --------------------------------------------------------------------------
# Strategii kampanij: chto i kak chitaetsya (otkryto 22.09.2026)
#
# BiddingStrategy, PriorityGoals, CounterIds i Settings NE vhodyat v obshchij
# FieldNames servisa campaigns. Ih peredayut OTDEL'NYMI naborami polej:
#   TextCampaignFieldNames / UnifiedCampaignFieldNames / SmartCampaignFieldNames.
# Esli polozhit' "TextCampaign" v FieldNames, API otvechaet oshibkoj 8000
# i v tekste perechislyaet dopustimye podpolya — tak etot sposob i nashёlsya.
# Vazhnaya detal': u Smart-kampanij pole nazyvaetsya CounterId (v ed. chisle),
# poetomu u nih svoj nabor polej.
# --------------------------------------------------------------------------
STRATEGY_SUBFIELDS = [
    "CounterIds", "PriorityGoals", "BiddingStrategy", "AttributionModel",
    "Settings", "PackageBiddingStrategy", "WeeklyBudgetRollover",
]
SMART_SUBFIELDS = [
    "CounterId", "PriorityGoals", "BiddingStrategy", "AttributionModel",
    "Settings", "PackageBiddingStrategy", "WeeklyBudgetRollover",
]
STRATEGY_FIELDS = ["Id", "Name", "Type", "State", "Status", "DailyBudget"]
STRATEGY_BLOCKS = ("TextCampaign", "UnifiedCampaign", "SmartCampaign")
SCOPES = ("Search", "Network")
IDS_PER_REQUEST = 10        # Direct prinimaet ne bolee 10 ID za zapros

# Vnutrennij ob'ekt strategii: u kazhdogo tipa svoi limit i stavki.
STRATEGY_INNER = {
    "PAY_FOR_CONVERSION": "PayForConversion",
    "PAY_FOR_CONVERSION_MULTIPLE_GOALS": "PayForConversionMultipleGoals",
    "WB_MAXIMUM_CLICKS": "WbMaximumClicks",
    "WB_MAXIMUM_CONVERSION_RATE": "WbMaximumConversionRate",
    "WB_MAXIMUM_CONVERSIONS": "WbMaximumConversions",
    "AVERAGE_CPC": "AverageCpc",
    "AVERAGE_CPA": "AverageCpa",
    "AVERAGE_CRR": "AverageCrr",
    "HIGHEST_POSITION": "HighestPosition",
}

# Klassifikatsiya tipov strategij dlya proverok.
AUTO_TYPES = {
    "PAY_FOR_CONVERSION", "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
    "WB_MAXIMUM_CLICKS", "WB_MAXIMUM_CONVERSION_RATE", "WB_MAXIMUM_CONVERSIONS",
    "AVERAGE_CPA", "AVERAGE_CRR",
}
MANUAL_TYPES = {"HIGHEST_POSITION", "AVERAGE_CPC"}
OFF_TYPE = "SERVING_OFF"
FOLLOW_TYPES = {"NETWORK_DEFAULT"}      # "kak v poiske"


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
    chtoby srazu pisat' "ne provereno".
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


# --------------------------------------------------------------------------
# Strategii kampanij: chtenie cherez API
# --------------------------------------------------------------------------

def rub(value):
    """Mikroedinitsy → rubli. None ostаётся None (a ne nulyom)."""
    if value is None:
        return None
    try:
        return round(float(value) / MICROS, 2)
    except (TypeError, ValueError):
        return None


def _items(value) -> list:
    """Pole-spisok API prihodit kak {"Items": [...]}, rezhe kak obychyj list."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return list(value.get("Items") or [])
    return [value]


def _goal_id(value):
    """Identifikator tseli iz {GoalId: ...} libo iz gologo chisla."""
    if isinstance(value, dict):
        value = value.get("GoalId")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _strategy_scope(scope_obj) -> dict:
    """
    Odin kanal (Search / Network): tip strategii, limit rashoda, stavki i tseli.

    U kazhdogo tipa strategii svoj vnutrennij ob'ekt (PayForConversion,
    WbMaximumClicks, AverageCpc...). Berem ego po tablitse STRATEGY_INNER,
    a esli tip neznakomyj — pervyj zhe vlozhennyj ob'ekt.
    """
    if not isinstance(scope_obj, dict):
        return {"type": None, "weekly_limit": None, "bid_ceiling": None,
                "cpa": None, "average_cpc": None, "budget_type": None,
                "goals": [], "raw": {}}

    stype = scope_obj.get("BiddingStrategyType")
    inner = scope_obj.get(STRATEGY_INNER.get(str(stype)) or "")
    if not isinstance(inner, dict):
        inner = next((v for v in scope_obj.values() if isinstance(v, dict)), {})

    goals = []
    gid = _goal_id(inner.get("GoalId"))
    if gid is not None:
        goals.append(gid)
    for key in ("Goals", "GoalIds", "PriorityGoals"):
        for item in _items(inner.get(key)):
            gid = _goal_id(item)
            if gid is not None:
                goals.append(gid)

    return {
        "type": stype,
        "weekly_limit": rub(inner.get("WeeklySpendLimit")),
        "bid_ceiling": rub(inner.get("BidCeiling")),
        "cpa": rub(inner.get("Cpa")),
        "average_cpc": rub(inner.get("AverageCpc")),
        "budget_type": inner.get("BudgetType"),
        "goals": list(dict.fromkeys(goals)),
        "raw": scope_obj,
    }


def campaign_strategy(camp: dict) -> dict:
    """
    Razbor odnogo otveta campaigns.get v ponyatnuyu strukturu.

    Vozvrashchaet diktat po kampanii: kanaly, limity, tseli, schetchiki,
    nastroyki i syroj blok strategii (nuzhen dlya snimkov i otkatov).
    """
    block = next((camp.get(k) for k in STRATEGY_BLOCKS if camp.get(k)), None) or {}

    priority = []
    # В приоритетных целях лежит ещё и цена: Value (микроединицы) — именно её
    # алгоритм использует как максимум за заявку. Без неё нельзя понять, почему
    # «оплата за конверсии» не покупает трафик (250 ₽ при рынке 2 700 ₽).
    priority_values = {}
    priority_values_micro = {}
    for item in _items(block.get("PriorityGoals")):
        gid = _goal_id(item)
        if gid is not None:
            priority.append(gid)
        if gid is None or not isinstance(item, dict):
            continue
        value = item.get("Value")
        if isinstance(value, (int, float)):
            priority_values_micro[gid] = int(value)
            priority_values[gid] = rub(value)

    bidding = block.get("BiddingStrategy") or {}
    scopes = {scope: _strategy_scope(bidding.get(scope)) for scope in SCOPES}
    strategy_goals = []
    for scope in SCOPES:
        strategy_goals.extend(scopes[scope]["goals"])

    settings = {}
    for item in _items(block.get("Settings")):
        if isinstance(item, dict) and item.get("Option"):
            settings[str(item["Option"])] = item.get("Value")

    counters = []
    for key in ("CounterIds", "CounterId"):
        value = block.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            counters.extend(int(c) for c in _items(value) if c is not None)
        else:
            counters.append(int(value))

    daily = camp.get("DailyBudget")
    if isinstance(daily, dict):
        daily = daily.get("Amount")

    return {
        "id": camp.get("Id"),
        "name": camp.get("Name"),
        "type": camp.get("Type"),
        "state": camp.get("State"),
        "status": camp.get("Status"),
        "daily_budget": rub(daily),
        "attribution_model": block.get("AttributionModel"),
        "counter_ids": counters,
        "priority_goals": priority,
        "priority_goal_values": priority_values,
        "priority_goal_values_micro": priority_values_micro,
        "strategy_goals": {scope: scopes[scope]["goals"] for scope in SCOPES},
        "goals_all": list(dict.fromkeys(priority + strategy_goals)),
        "scopes": scopes,
        "settings": settings,
        "package_strategy": block.get("PackageBiddingStrategy"),
        "strategy_block": next((k for k in STRATEGY_BLOCKS if camp.get(k)), None),
        "raw": block,
    }


def campaign_strategies(account: str | None = None,
                        campaign_ids=None) -> dict:
    """
    Strategii, prioritetnye tseli, schetchiki i nastroyki kampanij — iz API.

    Vozvrashchaet {"campaigns": {campaign_id: {...}}, "requests": N} libo
    {"error": "..."}. Kampanii zaprashivaem po 10 ID: bolshe Direct ne prinimaet.

    Bez etogo chteniya audit ne znaet ni prioritetnyh tselej, ni nedel'nogo
    limita rashoda — a bez nih nevozmozhno otvetit' na vopros «kuda idut den'gi».
    """
    try:
        client = direct(account)
    except AccessError as e:
        return {"error": str(e)}

    ids = []
    for value in campaign_ids or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        listing = client.post("campaigns", "get",
                              {"SelectionCriteria": {}, "FieldNames": ["Id"]})
        if isinstance(listing, dict) and listing.get("error"):
            return {"error": str(listing.get("error_text") or listing.get("error"))}
        ids = [int(c["Id"]) for c in (listing.get("result") or {}).get("Campaigns", [])
               if c.get("Id")]
    if not ids:
        return {"campaigns": {}, "requests": 0, "subfields_param": None}

    # Lesenka naborov polej: esli API ne prinimaet kakoy-to nabor, probuem
    # sleduyushchij, a ne rонyaem ves audit.
    variants = [
        {"TextCampaignFieldNames": STRATEGY_SUBFIELDS,
         "UnifiedCampaignFieldNames": STRATEGY_SUBFIELDS,
         "SmartCampaignFieldNames": SMART_SUBFIELDS},
        {"TextCampaignFieldNames": STRATEGY_SUBFIELDS,
         "UnifiedCampaignFieldNames": STRATEGY_SUBFIELDS},
        {"TextCampaignFieldNames": STRATEGY_SUBFIELDS},
    ]

    campaigns = {}
    requests = 0
    working = None
    for start in range(0, len(ids), IDS_PER_REQUEST):
        chunk = ids[start:start + IDS_PER_REQUEST]
        order = ([working] if working else []) + [v for v in variants if v != working]
        last_error = None
        for variant in order:
            params = {"SelectionCriteria": {"Ids": chunk},
                      "FieldNames": STRATEGY_FIELDS}
            params.update(variant)
            result = client.post("campaigns", "get", params)
            requests += 1
            if isinstance(result, dict) and result.get("error"):
                last_error = str(result.get("error_text") or result.get("error"))
                continue
            working = variant
            for camp in (result.get("result") or {}).get("Campaigns", []):
                info = campaign_strategy(camp)
                if info.get("id"):
                    campaigns[int(info["id"])] = info
            break
        else:
            return {"error": last_error or "campaigns.get: неизвестная ошибка",
                    "campaigns": campaigns, "requests": requests}

    return {"campaigns": campaigns, "requests": requests,
            "subfields_param": ",".join(sorted((working or {}).keys())) or None}


def _registry_priority_goals(account: str | None = None) -> dict:
    """Prioritetnye tseli iz reestra — rezervnyj istochnik i predohranitel'."""
    _, record = context(account)
    raw = (record.get("direct") or {}).get("priority_goals") or {}
    result = {}
    for campaign_id, goals in raw.items():
        try:
            result[int(campaign_id)] = sorted({int(g) for g in goals})
        except (TypeError, ValueError):
            continue
    return result


def priority_goals_resolved(account: str | None = None,
                            strategies: dict | None = None) -> dict:
    """
    Prioritetnye tseli kampanij: snachala API, potom reestr.

    API pervyj, potomu chto reestr zastarevaet: tseli menяyut v interfejse ili
    drugim agentom, i ruchnye znacheniya tihо rashodyatsya s faktom. Reestr
    ostaetsya rezervom — na sluchaj, kogda strategiyu prochitat' ne udalos'.

    strategies — uzhe prochitannye dannye campaign_strategies (chtoby ne tratit'
    baly dva raza na odin i tot zhe zapros).

    Vozvrashchaet {"goals", "source", "api", "registry", "mismatch", "error"}.
    """
    try:
        registry = _registry_priority_goals(account)
    except AccessError:
        registry = {}

    error = None
    if strategies is None:
        data = campaign_strategies(account)
        if data.get("error"):
            error = data["error"]
        strategies = data.get("campaigns") or {}

    api_goals = {}
    for cid, info in (strategies or {}).items():
        goals = (info or {}).get("goals_all") or []
        if goals:
            api_goals[int(cid)] = list(goals)

    goals, mismatch = {}, {}
    for cid in sorted(set(api_goals) | set(registry)):
        from_api = sorted(api_goals.get(cid) or [])
        from_reg = sorted(registry.get(cid) or [])
        chosen = from_api or from_reg
        if not chosen:
            continue
        goals[cid] = chosen
        # Rassozhdenie — eto kogda reestr chto-to utverzhdaet, a API govorit
        # inoe. Pustoj reestr rassozhdeniem ne schitaem: eto nevypolnennaya
        # zapis', a ne ustarevshie dannye, i rugat'sya za nee nekogo.
        if from_reg and from_api != from_reg:
            mismatch[cid] = {"api": from_api, "registry": from_reg}

    if api_goals and mismatch:
        source = "mixed"
    elif api_goals:
        source = "api"
    elif registry:
        source = "registry"
    else:
        source = None

    return {"goals": goals, "source": source, "api": api_goals,
            "registry": registry, "mismatch": mismatch, "error": error}


def priority_goals(account: str | None = None) -> dict:
    """
    Prioritetnye tseli po kampaniyam: {campaign_id: [goal_id, ...]}.

    Chtenie cherez API (pole PriorityGoals v strategii kampanii), reestr —
    rezerv. Bez etih tselej analiz «den'gi v ploshchadki bez celevyh» schitaet
    konversii po VSEM tselyam schetchika i daet druguyu kartinu: s nimi my
    nahodim 1 748 ₽ rashoda bez tselenogo rezul'tata.
    """
    return priority_goals_resolved(account)["goals"]


def ytm_container_ids(account: str | None = None) -> list:
    _, acc = context(account)
    return list((acc.get("ytm") or {}).get("container_ids") or [])


def metrica_counter_ids(account: str | None = None) -> list:
    _, acc = context(account)
    return list((acc.get("metrica") or {}).get("counter_ids") or [])


def goal_names(account: str | None = None, counter_ids=None,
               goal_ids=None) -> dict:
    """
    Nazvaniya tselej Metriki: {goal_id: name}.

    Nuzhny vezde, gde pokazyvaem tseli: syrye ID bez imen chitat' nel'zya —
    imenno nazvanie otvechaet na vopros «za chto kampaniya platit».
    Schetchiki beryom iz kampanij (pole counter_ids); esli ne zadany —
    vse schetchiki akkaunta. Oshibka lyubogo schetchika ne ronyaet ostatok.
    """
    counters = []
    for value in counter_ids or []:
        try:
            counters.append(int(value))
        except (TypeError, ValueError):
            continue
    if not counters:
        counters = metrica_counter_ids(account)

    names = {}
    for counter_id in dict.fromkeys(counters):
        data = metrica_get(account, f"/management/v1/counter/{counter_id}/goals")
        if isinstance(data, dict) and data.get("error"):
            continue
        for g in data.get("goals") or []:
            try:
                gid = int(g.get("id"))
            except (TypeError, ValueError):
                continue
            names[gid] = g.get("name") or f"цель {gid}"

    if goal_ids:
        wanted = set()
        for value in goal_ids:
            try:
                wanted.add(int(value))
            except (TypeError, ValueError):
                continue
        names = {gid: name for gid, name in names.items() if gid in wanted}
    return names


def label_goals(names: dict, goal_ids) -> list:
    """Tsely dlya otveta: [«nazvanie (ID)», ...] — imya pered tsifroj."""
    result = []
    for value in goal_ids or []:
        try:
            gid = int(value)
        except (TypeError, ValueError):
            continue
        result.append(f"{names.get(gid) or 'цель ' + str(gid)} (ID {gid})")
    return result


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

from mcp_instance import mcp

import access


@mcp.tool()
def get_campaigns(campaign_ids: list[int] = None, account: str | None = None) -> dict:
    """Poluchaet spisok kampaniy s osnovnymi parametrami.

    Args:
        campaign_ids: Spisok ID kampaniy (esli ne ukazan - vse)
        account: Imya akkaunta ili lyuboj znakomyj ID (schetchik, kontejner).
                 Esli ne ukazan - akkaunt po umolchaniyu (DEFAULT_ACCOUNT).
    """
    try:
        client = access.direct(account)
    except access.AccessError as e:
        return {"error": str(e)}

    params = {
        "SelectionCriteria": {},
        "FieldNames": [
            "Id", "Name", "State", "Status", "StatusPayment",
            "Type", "DailyBudget", "StartDate", "EndDate"
        ]
    }
    if campaign_ids:
        params["SelectionCriteria"]["Ids"] = campaign_ids
    return client.post("campaigns", "get", params)


@mcp.tool()
def get_campaign_strategies(campaign_ids: list[int] = None,
                            account: str | None = None) -> dict:
    """
    Стратегии кампаний: тип по каналам, недельный лимит, ставки и приоритетные
    цели.

    Это то, что раньше считалось недоступным через API. Теперь видно, чем
    управляются ставки (автостратегия или ручной режим), сколько кампания
    может израсходовать за неделю, на какие цели Метрики она оптимизируется
    и какая у неё модель атрибуции.

    Каналы: Search — поиск, Network — РСЯ и сети. Стратегия NETWORK_DEFAULT
    означает «как в поиске».

    :param campaign_ids: список ID кампаний; без него — все кампании аккаунта
    :param account: имя аккаунта или любой знакомый ID (счётчик, контейнер, логин)
    """
    data = access.campaign_strategies(account, campaign_ids)
    if data.get("error"):
        return data

    # Tseli pokazyvayutsya s nazvaniyami: bez imen ID ne otvechayut na vopros
    # «za chto platit kampaniya». Oshibka Metriki ne ronyaet strategii.
    counters = []
    for info in (data.get("campaigns") or {}).values():
        counters.extend(info.get("counter_ids") or [])
    names = access.goal_names(account, counters)

    rows = []
    for cid, info in sorted((data.get("campaigns") or {}).items()):
        channels = {}
        for scope, scope_info in (info.get("scopes") or {}).items():
            channels[scope] = {
                "стратегия": scope_info.get("type"),
                "недельный лимит ₽": scope_info.get("weekly_limit"),
                "ограничение ставки ₽": scope_info.get("bid_ceiling"),
                "средняя цена клика ₽": scope_info.get("average_cpc"),
                "целевая цена конверсии ₽": scope_info.get("cpa"),
                "цели": access.label_goals(names, scope_info.get("goals")),
            }
        rows.append({
            "id": cid,
            "название": info.get("name"),
            "состояние": info.get("state"),
            "каналы": channels,
            "приоритетные цели": access.label_goals(names, info.get("priority_goals")),
            "цели стратегии": {
                scope: access.label_goals(names, goals)
                for scope, goals in (info.get("strategy_goals") or {}).items()
            },
            "модель атрибуции": info.get("attribution_model"),
            "счётчики Метрики": info.get("counter_ids"),
            "дневной бюджет ₽": info.get("daily_budget"),
            "пакетная стратегия": info.get("package_strategy"),
        })

    return {
        "campaigns": len(rows),
        "requests": data.get("requests"),
        "source": f"campaigns.get + {data.get('subfields_param')}",
        "rows": rows,
    }


@mcp.tool()
def check_api_connection(account: str | None = None) -> dict:
    """Proveryaet podklyuchenie k API Yandex.Direct dlya akkaunta."""
    try:
        client = access.direct(account)
    except access.AccessError as e:
        return {"error": str(e)}

    result = client.check_connection()
    if isinstance(result, dict) and result.get("error"):
        return result

    campaigns = (result.get("result") or {}).get("Campaigns", [])
    return {
        "ok": True,
        "mode": access.direct_mode(account),
        "client_login": client.client_login,
        "campaigns": len(campaigns),
        "units": client.units_info(),
        "request_id": client.last_request_id,
    }

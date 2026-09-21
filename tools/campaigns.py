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

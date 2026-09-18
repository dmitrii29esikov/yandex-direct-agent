from server import mcp, api_client


@mcp.tool()
def get_campaigns(campaign_ids: list[int] = None) -> dict:
    """Poluchaet spisok kampaniy s osnovnymi parametrami.

    Args:
        campaign_ids: Spisok ID kampaniy (esli ne ukazan - vse)
    """
    params = {
        "SelectionCriteria": {},
        "FieldNames": [
            "Id", "Name", "State", "Status", "StatusPayment",
            "Type", "DailyBudget", "StartDate", "EndDate"
        ]
    }
    if campaign_ids:
        params["SelectionCriteria"]["Ids"] = campaign_ids
    return api_client.post("campaigns", "get", params)


@mcp.tool()
def check_api_connection() -> dict:
    """Proveryaet podklyuchenie k API Yandex.Direct."""
    return api_client.check_connection()
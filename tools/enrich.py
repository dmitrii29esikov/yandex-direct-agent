from mcp_instance import mcp
# Obogashchenie dannyh: podstavlyaem nazvaniya vmesto ID.
# Kampanii (Direct), schetchiki i celi (Metrika).
# Kommentarii translitom, chtoby ne bylo krakozyabr v Windows-1251.

import access


@mcp.tool()
def get_campaign_names(campaign_ids: list[int] | None = None,
                       account: str | None = None) -> dict:
    """
    Vozvrashchaet {campaign_id: campaign_name} dlya vseh ili zadannyh kampanij.

    :param campaign_ids: spisok ID kampanij. Esli None — vse kampanii akkaunta.
    :param account: imya akkaunta ili lyuboj znakomyj ID.
    """
    try:
        client = access.direct(account)
    except access.AccessError as e:
        return {"error": str(e)}

    params = {"FieldNames": ["Id", "Name", "State", "Status"]}
    if campaign_ids:
        params["SelectionCriteria"] = {"Ids": [int(c) for c in campaign_ids]}

    result = client.post("campaigns", "get", params)
    if isinstance(result, dict) and "error" in result:
        return result

    campaigns = result.get("result", {}).get("Campaigns", [])
    mapping = {}
    details = {}
    for c in campaigns:
        cid = c.get("Id")
        name = c.get("Name")
        mapping[cid] = name
        details[cid] = {
            "name": name,
            "state": c.get("State"),
            "status": c.get("Status"),
        }
    return {"count": len(mapping), "mapping": mapping, "details": details}


@mcp.tool()
def get_counter_names(account: str | None = None) -> dict:
    """Vozvrashchaet {counter_id: counter_name} dlya vseh schetchikov Metriki."""
    data = access.metrica_get(account, "/management/v1/counters")
    if isinstance(data, dict) and data.get("error"):
        return {"error": data["error"]}

    mapping = {}
    details = {}
    for c in data.get("counters", []):
        cid = c.get("id")
        mapping[cid] = c.get("name")
        details[cid] = {
            "name": c.get("name"),
            "site": c.get("site"),
            "type": c.get("type"),
            "status": c.get("status"),
            "owner_login": c.get("owner_login"),
        }
    return {"count": len(mapping), "mapping": mapping, "details": details}


@mcp.tool()
def get_goal_names(counter_id: int, goal_ids: list[int] | None = None,
                   account: str | None = None) -> dict:
    """
    Vozvrashchaet {goal_id: goal_name} dlya ukazannogo schetchika Metriki.

    :param counter_id: ID schetchika Metriki
    :param goal_ids: spisok ID celej. Esli None — vse celi schetchika.
    :param account: imya akkaunta ili lyuboj znakomyj ID.
    """
    data = access.metrica_get(account, f"/management/v1/counter/{counter_id}/goals")
    if isinstance(data, dict) and data.get("error"):
        return {"error": data["error"]}

    goals = data.get("goals", [])
    if goal_ids:
        goal_set = {int(g) for g in goal_ids}
        goals = [g for g in goals if g.get("id") in goal_set]

    mapping = {}
    details = {}
    for g in goals:
        gid = g.get("id")
        mapping[gid] = g.get("name")
        details[gid] = {
            "name": g.get("name"),
            "type": g.get("type"),
            "is_favorite": g.get("is_favorite", False),
        }
    return {
        "counter_id": counter_id,
        "count": len(mapping),
        "mapping": mapping,
        "details": details,
    }

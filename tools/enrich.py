# tools/enrich.py
# Obogashchenie dannyh: podstavlyaem nazvaniya vmesto ID.
# Kampanii (Direct), schetchiki i celi (Metrika).
# Kommentarii translitom, chtoby ne bylo krakozyabr v Windows-1251.

import os
import requests
from dotenv import load_dotenv

from server import mcp, api_client

load_dotenv()


def _metrica_headers() -> dict | None:
    """Zagolovki dlya Metrika API. None, esli token ne zadan v .env."""
    token = os.getenv("YANDEX_METRICA_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
    }


@mcp.tool()
def get_campaign_names(campaign_ids: list[int] | None = None) -> dict:
    """
    Vozvrashchaet {campaign_id: campaign_name} dlya vseh ili zadannyh kampanij.

    :param campaign_ids: spisok ID kampanij. Esli None — vse kampanii akkaunta.
    """
    params = {"FieldNames": ["Id", "Name", "State", "Status"]}
    if campaign_ids:
        params["SelectionCriteria"] = {"Ids": [int(c) for c in campaign_ids]}

    result = api_client.post("campaigns", "get", params)
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
def get_counter_names() -> dict:
    """Vozvrashchaet {counter_id: counter_name} dlya vseh schetchikov Metriki."""
    headers = _metrica_headers()
    if headers is None:
        return {"error": "YANDEX_METRICA_TOKEN ne zadan v .env"}

    url = "https://api-metrika.yandex.net/management/v1/counters"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e)}

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
        }
    return {"count": len(mapping), "mapping": mapping, "details": details}


@mcp.tool()
def get_goal_names(counter_id: int, goal_ids: list[int] | None = None) -> dict:
    """
    Vozvrashchaet {goal_id: goal_name} dlya ukazannogo schetchika.

    :param counter_id: ID schetchika Metriki
    :param goal_ids: spisok ID celej. Esli None — vse celi schetchika.
    """
    headers = _metrica_headers()
    if headers is None:
        return {"error": "YANDEX_METRICA_TOKEN ne zadan v .env"}

    url = f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}/goals"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e)}

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
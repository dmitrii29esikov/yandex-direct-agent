from mcp_instance import mcp, api_client
import os
import requests
from dotenv import load_dotenv

load_dotenv()


def _classify_counter(counter: dict) -> dict:
    """Opredelyaet tip schetchika: Metrika, Yandex.Biznes ili Google Analytics."""
    site = (counter.get("site") or "").lower()
    ctype = (counter.get("type") or "").upper()

    if ctype == "GA":
        return {"kind": "ga", "kind_label": "Google Analytics", "icon": "[GA]"}

    if ("yandex.ru/maps" in site
            or "yandex.ru/sprav" in site
            or ctype == "YANDEX_BUSINESS"):
        return {"kind": "yandex_business", "kind_label": "Yandex.Biznes", "icon": "[YB]"}

    return {"kind": "metrica", "kind_label": "Yandex.Metrika", "icon": "[MC]"}


@mcp.tool()
def get_metrica_counters() -> dict:
    """Poluchaet spisok schetchikov s klassifikatsiey po 3 tipam:
    metrica, yandex_business, ga.
    """
    metrica_token = os.getenv("YANDEX_METRICA_TOKEN")
    if not metrica_token:
        return {"error": "YANDEX_METRICA_TOKEN ne zadan v .env"}

    url = "https://api-metrika.yandex.net/management/v1/counters"
    headers = {
        "Authorization": f"OAuth {metrica_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        counters = []
        for c in data.get("counters", []):
            classification = _classify_counter(c)
            counters.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "site": c.get("site"),
                "type": c.get("type"),
                "status": c.get("status"),
                "kind": classification["kind"],
                "kind_label": classification["kind_label"],
                "icon": classification["icon"]
            })

        grouped = {"metrica": [], "yandex_business": [], "ga": []}
        for c in counters:
            grouped[c["kind"]].append(c)

        return {"counters": counters, "total": len(counters), "grouped": grouped}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_metrica_goals(counter_id: int) -> dict:
    """Poluchaet spisok tseley dlya ukazannogo schetchika Metriki."""
    metrica_token = os.getenv("YANDEX_METRICA_TOKEN")
    if not metrica_token:
        return {"error": "YANDEX_METRICA_TOKEN ne zadan v .env"}

    url = f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}/goals"
    headers = {
        "Authorization": f"OAuth {metrica_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        goals = []
        for g in data.get("goals", []):
            goals.append({
                "id": g.get("id"),
                "name": g.get("name"),
                "type": g.get("type"),
                "is_favorite": g.get("is_favorite", False),
                "is_retargeting": g.get("is_retargeting", False)
            })

        return {"counter_id": counter_id, "goals": goals, "total": len(goals)}
    except Exception as e:
        return {"error": str(e)}
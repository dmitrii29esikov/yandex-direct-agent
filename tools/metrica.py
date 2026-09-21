from mcp_instance import mcp
# Yandex.Metrika Management API.
# Kommentarii translitom, chtoby ne bylo krakozyabr v Windows-1251.

import access


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
def get_metrica_counters(account: str | None = None, linked_only: bool = False) -> dict:
    """
    Poluchaet spisok schetchikov s klassifikatsiej po 3 tipam:
    metrica, yandex_business, ga.

    :param account: imya akkaunta ili lyuboj znakomyj ID
    :param linked_only: tol'ko schetchiki, privyazannye k akkauntu v reestre
    """
    data = access.metrica_get(account, "/management/v1/counters")
    if isinstance(data, dict) and data.get("error"):
        return {"error": data["error"]}

    linked = access.metrica_counter_ids(account)
    counters = []
    for c in data.get("counters", []):
        if linked_only and linked and c.get("id") not in linked:
            continue
        classification = _classify_counter(c)
        counters.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "site": c.get("site"),
            "type": c.get("type"),
            "status": c.get("status"),
            "activity_status": c.get("activity_status"),
            "code_status": c.get("code_status"),
            "owner_login": c.get("owner_login"),
            "permission": c.get("permission"),
            "kind": classification["kind"],
            "kind_label": classification["kind_label"],
            "icon": classification["icon"],
        })

    grouped = {"metrica": [], "yandex_business": [], "ga": []}
    for c in counters:
        grouped[c["kind"]].append(c)

    return {"counters": counters, "total": len(counters), "grouped": grouped}


@mcp.tool()
def get_metrica_goals(counter_id: int, account: str | None = None) -> dict:
    """
    Poluchaet spisok tseley dlya ukazannogo schetchika Metriki.

    :param counter_id: ID schetchika Metriki
    :param account: imya akkaunta ili lyuboj znakomyj ID
    """
    data = access.metrica_get(account, f"/management/v1/counter/{counter_id}/goals")
    if isinstance(data, dict) and data.get("error"):
        return {"error": data["error"]}

    goals = []
    for g in data.get("goals", []):
        goals.append({
            "id": g.get("id"),
            "name": g.get("name"),
            "type": g.get("type"),
            "is_favorite": g.get("is_favorite", False),
            "is_retargeting": g.get("is_retargeting", False),
        })

    return {"counter_id": counter_id, "goals": goals, "total": len(goals)}

from mcp_instance import mcp, api_client
# Yandex Tag Manager (YTM) API client.
# Kommentarii translitom, chtoby ne bylo krakozyabr v Windows-1251.
#
# API: https://api.ytm.yandex.net/ytm/management/v1/container/{id}/...
# Avtorizatsiya: OAuth-token s dostupom "ytm:read".

import os
import requests
from dotenv import load_dotenv

from server import mcp

load_dotenv()

YTM_BASE = "https://api.ytm.yandex.net/ytm/management/v1"


def _ytm_headers() -> dict | None:
    """Zagolovki dlya YTM API. None, esli token ne zadan v .env."""
    token = os.getenv("YANDEX_YTM_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _ytm_get(path: str) -> dict:
    """Universal'nyj GET k YTM API."""
    headers = _ytm_headers()
    if headers is None:
        return {"error": "YANDEX_YTM_TOKEN ne zadan v .env"}

    url = f"{YTM_BASE}/{path}"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}

    if resp.status_code == 401:
        return {"error": "401 Unauthorized: prover' token YANDEX_YTM_TOKEN "
                         "i ego dostup 'ytm:read'"}
    if resp.status_code == 403:
        return {"error": "403 Forbidden: u tokena net dostupa k etomu "
                         "kontejneru"}
    if resp.status_code == 404:
        return {"error": f"404 Not Found: {path}"}
    if resp.status_code == 429:
        return {"error": "429 Too Many Requests: prevyshen limit "
                         "5000 zaprosov v sutki"}
    if resp.status_code != 200:
        return {"error": f"YTM API {resp.status_code}: {resp.text[:500]}"}

    try:
        return resp.json()
    except ValueError:
        return {"error": "YTM API otvet ne JSON", "raw": resp.text[:500]}


def _is_builtin_variable(v: dict) -> bool:
    """
    Vstroennye peremennye YTM imeyut strokovyj variable_id
    (page_url, referrer, random_number), a pol'zovatel'skie — chislovoj
    (1030440, 902429). Ispol'zuem eto, chtoby ne shumet' v audit.
    """
    vid = str(v.get("variable_id") or "")
    return not vid.isdigit()


@mcp.tool()
def get_ytm_tags(container_id: int) -> dict:
    """Spisok tegov kontejnera YTM."""
    data = _ytm_get(f"container/{container_id}/tags")
    if "error" in data and "tags" not in data:
        return data

    tags = []
    for t in data.get("tags", []):
        tags.append({
            "tag_id": t.get("tag_id"),
            "name": t.get("name"),
            "type": t.get("type"),
            "status": t.get("status"),
            "triggers": t.get("triggers", []),
            "links_number": t.get("links_number"),
            "updated_by": t.get("updated_by"),
            "update_time": t.get("update_time"),
        })

    return {
        "container_id": container_id,
        "count": len(tags),
        "tags": tags,
        "total_from_api": data.get("total"),
    }


@mcp.tool()
def get_ytm_triggers(container_id: int) -> dict:
    """Spisok trigerov kontejnera YTM."""
    data = _ytm_get(f"container/{container_id}/triggers")
    if "error" in data and "triggers" not in data:
        return data

    triggers = []
    for t in data.get("triggers", []):
        triggers.append({
            "trigger_id": t.get("trigger_id"),
            "name": t.get("name"),
            "type": t.get("type"),
            "status": t.get("status"),
            "links_number": t.get("links_number"),
            "updated_by": t.get("updated_by"),
            "update_time": t.get("update_time"),
        })

    return {
        "container_id": container_id,
        "count": len(triggers),
        "triggers": triggers,
        "total_from_api": data.get("total"),
    }


@mcp.tool()
def get_ytm_variables(container_id: int, only_user: bool = False) -> dict:
    """
    Spisok peremennyh kontejnera YTM.

    :param container_id: ID kontejnera
    :param only_user: esli True — vozvraschaem tol'ko pol'zovatel'skie
                      peremennye (bez vstroennyh Page URL, Referrer i t.p.)
    """
    data = _ytm_get(f"container/{container_id}/variables")
    if "error" in data and "variables" not in data:
        return data

    variables = []
    for v in data.get("variables", []):
        if only_user and _is_builtin_variable(v):
            continue
        variables.append({
            "variable_id": v.get("variable_id"),
            "name": v.get("name"),
            "type": v.get("type"),
            "status": v.get("status"),
            "links_number": v.get("links_number"),
            "updated_by": v.get("updated_by"),
            "update_time": v.get("update_time"),
            "is_builtin": _is_builtin_variable(v),
        })

    return {
        "container_id": container_id,
        "count": len(variables),
        "variables": variables,
        "total_from_api": data.get("total"),
    }


@mcp.tool()
def audit_ytm_container(container_id: int) -> dict:
    """
    Audit kontejnera YTM:
    - tegi bez trigerov;
    - triggery bez svyazej (links_number == 0);
    - neispol'zuemye pol'zovatel'skie peremennye (links_number == 0);
    - dublikaty po nazvaniyu;
    - obschaya svodka.

    Vstroennye peremennye (Page URL, Referrer i t.p.) v audit ne popadayut —
    u nih links_number vsegda 0 po prirode YTM.
    """
    tags_data = get_ytm_tags(container_id)
    triggers_data = get_ytm_triggers(container_id)
    variables_data = get_ytm_variables(container_id)

    for d in (tags_data, triggers_data, variables_data):
        if "error" in d and not any(k in d for k in ("tags", "triggers", "variables")):
            return d

    tags = tags_data.get("tags", [])
    triggers = triggers_data.get("triggers", [])
    variables = variables_data.get("variables", [])

    # 1. Tegi bez trigerov
    tags_without_triggers = [
        {"name": t["name"], "tag_id": t["tag_id"]}
        for t in tags
        if not t.get("triggers")
    ]

    # 2. Triggery bez svyazej (nikto ne ssylaetsya)
    triggers_unused = [
        {"name": t["name"], "trigger_id": t["trigger_id"]}
        for t in triggers
        if not t.get("links_number")
    ]

    # 3. Neispol'zuemye pol'zovatel'skie peremennye (vstroennye propuskaem)
    variables_unused = [
        {"name": v["name"], "variable_id": v["variable_id"],
         "type": v.get("type")}
        for v in variables
        if not v.get("links_number") and not v.get("is_builtin")
    ]

    builtin_variables_count = sum(1 for v in variables if v.get("is_builtin"))

    # 4. Dublikaty po nazvaniyu
    def _dupes(items, key):
        seen = {}
        for it in items:
            n = it.get("name")
            seen.setdefault(n, []).append(it.get(key))
        return [{"name": n, "ids": ids} for n, ids in seen.items() if len(ids) > 1]

    dup_tags = _dupes(tags, "tag_id")
    dup_triggers = _dupes(triggers, "trigger_id")
    dup_variables = _dupes(
        [v for v in variables if not v.get("is_builtin")], "variable_id"
    )

    return {
        "container_id": container_id,
        "summary": {
            "tags": len(tags),
            "triggers": len(triggers),
            "variables_user": len(variables) - builtin_variables_count,
            "variables_builtin": builtin_variables_count,
        },
        "issues": {
            "tags_without_triggers": tags_without_triggers,
            "triggers_unused": triggers_unused,
            "variables_unused": variables_unused,
            "duplicate_tags": dup_tags,
            "duplicate_triggers": dup_triggers,
            "duplicate_variables": dup_variables,
        },
        "healthy": (
            not tags_without_triggers
            and not triggers_unused
            and not variables_unused
            and not dup_tags
            and not dup_triggers
            and not dup_variables
        ),
    }
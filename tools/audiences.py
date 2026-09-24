from mcp_instance import mcp

import access
from audit_engine import audiences as aud


def _campaigns(account):
    client = access.direct(account)
    result = client.post("campaigns", "get",
                         {"SelectionCriteria": {"States": ["ON"]},
                          "FieldNames": ["Id", "Name", "State"]})
    return ((result or {}).get("result") or {}).get("Campaigns") or []


@mcp.tool()
def audience_snapshot(account: str | None = None, only_active: bool = True) -> dict:
    """Снимок аудиторий, условий ретаргетинга и корректировок ставок.

    Эти настройки не попадают в обычный снимок кампаний, хотя именно они
    меняют охват: подключение и отключение аудиторий, правка условий
    ретаргетинга, корректировки ставок по устройствам, демографии и сегментам.
    Читаются сервисами API retargetinglists, audiencetargets и bidmodifiers.

    Args:
        account: имя аккаунта или любой знакомый ID
        only_active: True — только запущенные кампании (State = ON)
    """
    try:
        campaign_ids = [c["Id"] for c in _campaigns(account)]
    except access.AccessError as e:
        return {"error": str(e)}
    if not campaign_ids:
        return {"error": "нет запущенных кампаний"}

    report = aud.collect(account, campaign_ids)
    path = aud.save_snapshot(account or "default", report)
    return {
        "снимок": path,
        "кампаний": len(report["campaigns"]),
        "условий ретаргетинга": len(report["lists"]),
        "аудиторий подключено": len(report["audiences"]),
        "корректировок ставок": len(report["corrections"]),
        "примеры аудиторий": [
            {"list_id": a["list_id"], "state": a["state"],
             "name": (report["lists"].get(a["list_id"]) or {}).get("name")}
            for a in report["audiences"][:8]],
        "корректировки": [
            f"{c['level']} / {c['type']} (кампания {c['campaign_id']})"
            for c in report["corrections"][:12]],
        "ошибки": report["errors"],
    }


@mcp.tool()
def audience_changes(account: str | None = None,
                     compare_with: str | None = None,
                     only_active: bool = True) -> dict:
    """Что изменилось в аудиториях, ретаргетинге и корректировках.

    Сравнивает текущее состояние с последним снимком (или с указанным файлом).
    Показывает подключённые и отключённые аудитории, изменения условий
    ретаргетинга и корректировок ставок — то, чего не видно в отчётах.

    Args:
        account: имя аккаунта или любой знакомый ID
        compare_with: имя файла снимка из data/snapshots (по умолчанию — свежий)
        only_active: True — только запущенные кампании
    """
    previous_path = aud.last_snapshot(account or "default", compare_with)
    if not previous_path:
        return {"error": "нет сохранённых снимков аудиторий — сначала сделайте "
                         "снимок инструментом audience_snapshot"}
    try:
        campaign_ids = [c["Id"] for c in _campaigns(account)]
    except access.AccessError as e:
        return {"error": str(e)}
    if not campaign_ids:
        return {"error": "нет запущенных кампаний"}

    with open(previous_path, encoding="utf-8") as fh:
        import json
        previous = json.load(fh)
    current = aud.collect(account, campaign_ids)
    changes = aud.diff(previous, current)
    return {
        "сравнение с": previous_path,
        "изменений": len(changes),
        "изменения": changes[:60],
        "итого сейчас": {
            "условий ретаргетинга": len(current["lists"]),
            "аудиторий подключено": len(current["audiences"]),
            "корректировок ставок": len(current["corrections"]),
        },
        "ошибки": current["errors"],
    }

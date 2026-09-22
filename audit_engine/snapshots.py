"""
Снимки конфигурации кампаний, сравнение и заготовки откатов.

Зачем: отчёт об изменениях за период — сильнейшая часть аудита. Без него
невозможно понять, что случилось с аккаунтом за неделю и кто это сделал.

ВАЖНОЕ ОГРАНИЧЕНИЕ. Инлайн-стратегия кампании (BiddingStrategy, PriorityGoals)
через API не читается: сервис campaigns не поддерживает поле TextCampaign,
API v4 отключён, а сервис strategies отдаёт только пакетные стратегии. Поэтому
снимок отслеживает состояние, бюджет, минус-фразы, временной таргетинг и
количество объектов, но НЕ смену стратегии. Это честно указано в отчёте.

Все функции только читают аккаунт. Откаты — это текст подсказок, они не
отправляются в API: применение правок делается отдельно и по подтверждению.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import access

log = logging.getLogger("audit.snapshots")

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"

# Поля кампании, которые реально доступны в campaigns.get.
CAMPAIGN_FIELDS = ["Id", "Name", "Type", "State", "Status", "StatusPayment",
                   "StatusClarification", "StartDate", "EndDate", "DailyBudget",
                   "NegativeKeywords", "TimeTargeting", "TimeZone"]

TRACKED = ["State", "Status", "StatusPayment", "StatusClarification",
           "StartDate", "EndDate", "DailyBudget", "TimeZone"]


def _listify(value):
    """Поле-список приходит как {'Items': [...]}, реже как обычный список."""
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.get("Items") or [])
    if isinstance(value, list):
        return list(value)
    return [value]


def build_snapshot(account: str, date_range: str | None = None) -> dict:
    """
    Собираем текущее состояние кампаний аккаунта.

    Возвращает {"error": ...} при сбое доступа, иначе структуру со снимком.
    """
    try:
        client = access.direct(account)
    except access.AccessError as e:
        return {"error": str(e)}

    result = client.post("campaigns", "get",
                         {"SelectionCriteria": {}, "FieldNames": CAMPAIGN_FIELDS})
    if isinstance(result, dict) and result.get("error"):
        return {"error": str(result.get("error_text") or result.get("error"))}

    campaigns = {}
    for camp in (result.get("result") or {}).get("Campaigns", []):
        cid = str(camp.get("Id"))
        campaigns[cid] = {
            "Id": camp.get("Id"),
            "Name": camp.get("Name"),
            "Type": camp.get("Type"),
            "State": camp.get("State"),
            "Status": camp.get("Status"),
            "StatusPayment": camp.get("StatusPayment"),
            "StatusClarification": camp.get("StatusClarification"),
            "StartDate": camp.get("StartDate"),
            "EndDate": camp.get("EndDate"),
            "DailyBudget": camp.get("DailyBudget"),
            "TimeZone": camp.get("TimeZone"),
            "TimeTargeting": camp.get("TimeTargeting"),
            "NegativeKeywords": _listify(camp.get("NegativeKeywords")),
        }

    # Количество групп и объявлений — тоже часть состояния аккаунта.
    for cid in campaigns:
        campaigns[cid]["AdGroups"] = None
        campaigns[cid]["Ads"] = None
    groups = client.post("adgroups", "get",
                         {"SelectionCriteria": {"CampaignIds": [
                             c["Id"] for c in campaigns.values()][:10]},
                          "FieldNames": ["Id", "CampaignId"],
                          "Page": {"Limit": 10000}})
    if not (isinstance(groups, dict) and groups.get("error")):
        counts = {}
        for grp in (groups.get("result") or {}).get("AdGroups", []):
            counts[str(grp.get("CampaignId"))] = counts.get(str(grp.get("CampaignId")), 0) + 1
        for cid, count in counts.items():
            if cid in campaigns:
                campaigns[cid]["AdGroups"] = count

    return {
        "account": account,
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "date_range": date_range,
        "campaigns": campaigns,
        "limitation": "Смена стратегии не отслеживается: инлайн-стратегия "
                      "не читается через API.",
    }


def save_snapshot(account: str, date_range: str | None = None) -> dict:
    """Снимаем состояние и кладём в data/snapshots/direct_<аккаунт>_<время>.json."""
    snapshot = build_snapshot(account, date_range)
    if snapshot.get("error"):
        return snapshot

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = SNAPSHOTS_DIR / f"direct_{account}_{stamp}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    return {"ok": True, "file": path.name, "path": str(path),
            "taken_at": snapshot["taken_at"],
            "campaigns": len(snapshot["campaigns"])}


def list_snapshots(account: str | None = None) -> dict:
    """Список снимков, свежие сверху."""
    if not SNAPSHOTS_DIR.exists():
        return {"count": 0, "snapshots": []}
    items = []
    for path in sorted(SNAPSHOTS_DIR.glob("direct_*.json"), reverse=True):
        if account and f"direct_{account}_" not in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        items.append({"file": path.name, "taken_at": data.get("taken_at"),
                      "account": data.get("account"),
                      "campaigns": len(data.get("campaigns") or {})})
    return {"count": len(items), "snapshots": items}


def _latest_snapshot(account: str) -> dict | None:
    items = list_snapshots(account)["snapshots"]
    if not items:
        return None
    path = SNAPSHOTS_DIR / items[0]["file"]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _rollback_state(campaign_id: int, state: str) -> str:
    method = "suspend" if state in ("OFF", "SUSPENDED") else "resume"
    return json.dumps({"method": method,
                       "params": {"SelectionCriteria": {"Ids": [campaign_id]}}},
                      ensure_ascii=False)


def _rollback_budget(campaign_id: int, budget) -> str:
    return json.dumps({"method": "update",
                       "params": {"Campaigns": [{"Id": campaign_id,
                                                 "DailyBudget": budget}]}},
                      ensure_ascii=False)


def _rollback_negatives(campaign_id: int, keywords: list) -> str:
    return json.dumps({
        "method": "update",
        "params": {"Campaigns": [{"Id": campaign_id,
                                  "NegativeKeywords": {"Items": keywords}}]}},
        ensure_ascii=False)


def _diff_campaign(before: dict, after: dict) -> list:
    changes = []
    campaign_id = after.get("Id")
    name = after.get("Name") or before.get("Name")

    for field in TRACKED:
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        item = {"campaign_id": campaign_id, "campaign_name": name,
                "field": field, "before": old, "after": new}
        if field == "State":
            item["rollback"] = _rollback_state(campaign_id, old)
            item["severity"] = "warning"
            item["note"] = "Кампания включена/остановлена — если не планировалось, верните"
        elif field == "DailyBudget":
            item["rollback"] = _rollback_budget(campaign_id, old)
            item["severity"] = "warning"
            item["note"] = "Дневной бюджет изменён"
        else:
            item["severity"] = "info"
        changes.append(item)

    old_neg = set(before.get("NegativeKeywords") or [])
    new_neg = set(after.get("NegativeKeywords") or [])
    if old_neg != new_neg:
        added, removed = sorted(new_neg - old_neg), sorted(old_neg - new_neg)
        changes.append({
            "campaign_id": campaign_id, "campaign_name": name,
            "field": "NegativeKeywords", "severity": "info",
            "before": f"{len(old_neg)} минус-фраз",
            "after": f"{len(new_neg)} минус-фраз",
            "added": added[:30], "removed": removed[:30],
            "added_count": len(added), "removed_count": len(removed),
            "rollback": _rollback_negatives(campaign_id, sorted(old_neg)),
            "note": "Изменён список минус-фраз",
        })

    for field, label in (("AdGroups", "групп"), ("Ads", "объявлений")):
        old, new = before.get(field), after.get(field)
        if old is not None and new is not None and old != new:
            changes.append({"campaign_id": campaign_id, "campaign_name": name,
                            "field": field, "severity": "info",
                            "before": old, "after": new,
                            "note": f"Изменилось число {label}"})
    return changes


def compare_snapshots(account: str, before_file: str | None = None,
                      current: dict | None = None) -> dict:
    """
    Сравниваем текущее состояние с предыдущим снимком.

    before_file — имя файла снимка; если не задано, берём самый свежий
    сохранённый. current — уже собранный снимок (чтобы не читать дважды).
    """
    if before_file:
        path = SNAPSHOTS_DIR / before_file
        if not path.exists():
            return {"error": f"Снимок не найден: {before_file}"}
        before = json.loads(path.read_text(encoding="utf-8"))
    else:
        before = _latest_snapshot(account)
    if not before:
        return {"error": "Нет сохранённых снимков — сначала сделайте снимок "
                         "инструментом snapshot_campaigns"}

    if current is None:
        current = build_snapshot(account)
        if current.get("error"):
            return current

    old_camps = before.get("campaigns") or {}
    new_camps = current.get("campaigns") or {}

    changes = []
    for cid, after in new_camps.items():
        if cid in old_camps:
            changes.extend(_diff_campaign(old_camps[cid], after))
        else:
            changes.append({"campaign_id": after.get("Id"),
                            "campaign_name": after.get("Name"),
                            "field": "Создана", "severity": "info",
                            "note": "Кампания появилась после снимка"})
    for cid, old in old_camps.items():
        if cid not in new_camps:
            changes.append({"campaign_id": old.get("Id"),
                            "campaign_name": old.get("Name"),
                            "field": "Удалена", "severity": "warning",
                            "note": "Кампания была в снимке, но пропала из аккаунта"})

    order = {"warning": 0, "info": 1}
    changes.sort(key=lambda c: order.get(c.get("severity"), 2))

    return {
        "account": account,
        "before_file": before.get("taken_at"),
        "current_at": current.get("taken_at"),
        "changes_count": len(changes),
        "changes": changes,
        "limitation": current.get("limitation"),
    }

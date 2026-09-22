"""
Снимки конфигурации кампаний, сравнение и заготовки откатов.

Зачем: отчёт об изменениях за период — сильнейшая часть аудита. Без него
невозможно понять, что случилось с аккаунтом за неделю и кто это сделал.

С 22.09.2026 в снимок попадают стратегии: тип по каналам, недельный лимит
расхода, ограничение ставки, целевая цена конверсии, приоритетные цели,
модель атрибуции и счётчики. Это стало возможно потому, что campaigns.get
принимает дополнительные наборы полей (TextCampaignFieldNames и родственные) —
см. access.campaign_strategies.

Что по-прежнему не отслеживается: связь «пакетная стратегия → кампания».
Сервис strategies отдаёт только сами пакеты, без списка кампаний на них.

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

# Подполя стратегии, которые сравниваем между снимками.
STRATEGY_TRACKED = [
    ("weekly_limit", "недельный лимит расхода", "warning"),
    ("bid_ceiling", "ограничение ставки", "info"),
    ("cpa", "целевая цена конверсии", "info"),
    ("average_cpc", "средняя цена клика", "info"),
    ("goals", "цели канала", "warning"),
]

ID_CHUNK = 10       # Direct принимает не более 10 ID за запрос


def _listify(value):
    """Поле-список приходит как {'Items': [...]}, реже как обычный список."""
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.get("Items") or [])
    if isinstance(value, list):
        return list(value)
    return [value]


def _strategy_snapshot(info: dict) -> dict:
    """Стратегия кампании в виде, пригодном для сравнения и отката."""
    return {
        scope: {
            "type": scope_info.get("type"),
            "weekly_limit": scope_info.get("weekly_limit"),
            "bid_ceiling": scope_info.get("bid_ceiling"),
            "cpa": scope_info.get("cpa"),
            "average_cpc": scope_info.get("average_cpc"),
            "goals": scope_info.get("goals"),
            "raw": scope_info.get("raw"),
        }
        for scope, scope_info in (info.get("scopes") or {}).items()
    }


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
            # Стратегии и цели — из отдельного запроса, ниже.
            "Strategy": {},
            "StrategyBlock": None,
            "PriorityGoals": [],
            "PriorityGoalValues": {},
            "PriorityGoalValuesMicro": {},
            "AttributionModel": None,
            "CounterIds": [],
            "Settings": {},
        }

    # Стратегии, лимиты и приоритетные цели: то, что раньше считалось недоступным.
    strategy_error = None
    strategies = {}
    try:
        data = access.campaign_strategies(account, list(campaigns.keys()))
        if data.get("error"):
            strategy_error = data["error"]
        else:
            strategies = data.get("campaigns") or {}
    except access.AccessError as e:
        strategy_error = str(e)

    for cid, info in strategies.items():
        bucket = campaigns.get(str(cid))
        if not bucket:
            continue
        bucket["Strategy"] = _strategy_snapshot(info)
        bucket["StrategyBlock"] = info.get("strategy_block")
        bucket["PriorityGoals"] = info.get("priority_goals") or []
        bucket["PriorityGoalValues"] = info.get("priority_goal_values") or {}
        bucket["PriorityGoalValuesMicro"] = info.get("priority_goal_values_micro") or {}
        bucket["AttributionModel"] = info.get("attribution_model")
        bucket["CounterIds"] = info.get("counter_ids") or []
        bucket["Settings"] = info.get("settings") or {}

    # Количество групп и объявлений — тоже часть состояния аккаунта.
    for cid in campaigns:
        campaigns[cid]["AdGroups"] = None
        campaigns[cid]["Ads"] = None
    ids = [c["Id"] for c in campaigns.values()]
    counts = {}
    for start in range(0, len(ids), ID_CHUNK):
        chunk = ids[start:start + ID_CHUNK]
        groups = client.post("adgroups", "get",
                             {"SelectionCriteria": {"CampaignIds": chunk},
                              "FieldNames": ["Id", "CampaignId"],
                              "Page": {"Limit": 10000}})
        if isinstance(groups, dict) and groups.get("error"):
            continue
        for grp in (groups.get("result") or {}).get("AdGroups", []):
            key = str(grp.get("CampaignId"))
            counts[key] = counts.get(key, 0) + 1
    for cid, count in counts.items():
        if cid in campaigns:
            campaigns[cid]["AdGroups"] = count

    if strategy_error:
        limitation = (f"Стратегии в этот снимок не попали: {strategy_error}. "
                      f"Восстановите доступ к Direct и снимите снимок заново")
    else:
        limitation = ("Связь «пакетная стратегия → кампания» не отслеживается: "
                      "сервис strategies отдаёт только пакеты стратегий")

    return {
        "account": account,
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "date_range": date_range,
        "campaigns": campaigns,
        "limited": bool(strategy_error),
        "limitation": limitation,
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
            "campaigns": len(snapshot["campaigns"]),
            "strategies": sum(1 for c in snapshot["campaigns"].values()
                              if c.get("Strategy")),
            "limitation": snapshot["limitation"]}


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


def _rollback_strategy(campaign_id: int, block: str | None,
                       scope: str, raw: dict | None) -> str | None:
    """
    Заготовка отката стратегии канала: campaigns.update с прежним BiddingStrategy.

    Возвращаем прежний объект канала целиком — вместе с лимитом, ставкой
    и целями. Именно поэтому снимок хранит "raw": восстановить по числам
    не получится, API ждёт исходную структуру.
    """
    if not block or not raw:
        return None
    return json.dumps({
        "method": "update",
        "params": {"Campaigns": [{"Id": campaign_id,
                                  block: {"BiddingStrategy": {scope: raw}}}]}},
        ensure_ascii=False)


def _diff_strategy(before: dict, after: dict, campaign_id, name: str) -> list:
    """
    Что изменилось в стратегиях кампании: тип канала, лимиты, ставки и цели.

    Если сменился тип стратегии канала, подполя этого канала не сравниваем:
    у новой стратегии другой набор лимитов, и такие «изменения» — шум.
    """
    changes = []
    block = after.get("StrategyBlock") or before.get("StrategyBlock")
    old_scopes = before.get("Strategy") or {}
    new_scopes = after.get("Strategy") or {}

    for scope in sorted(set(old_scopes) | set(new_scopes)):
        old = old_scopes.get(scope) or {}
        new = new_scopes.get(scope) or {}
        if old == new:
            continue

        if old.get("type") != new.get("type"):
            changes.append({
                "campaign_id": campaign_id, "campaign_name": name,
                "field": f"Стратегия канала {scope}", "severity": "warning",
                "before": old.get("type"), "after": new.get("type"),
                "rollback": _rollback_strategy(campaign_id, block, scope, old.get("raw")),
                "note": "Сменился тип стратегии — при откате вернётся и лимит, "
                        "и ставка, и цели этого канала",
            })
            continue

        for field, label, severity in STRATEGY_TRACKED:
            old_value, new_value = old.get(field), new.get(field)
            if old_value == new_value:
                continue
            item = {"campaign_id": campaign_id, "campaign_name": name,
                    "field": f"{label} ({scope})", "severity": severity,
                    "before": old_value, "after": new_value}
            if field in ("weekly_limit", "goals"):
                item["rollback"] = _rollback_strategy(campaign_id, block, scope,
                                                      old.get("raw"))
                item["note"] = ("Изменилась настройка стратегии: при откате "
                                "вернётся весь объект канала")
            elif field == "bid_ceiling":
                item["note"] = "Изменено ограничение ставки"
            else:
                item["note"] = f"Изменилась настройка: {label}"
            changes.append(item)

    # Цены целей: главный параметр закупки. Смена цены = смена того,
    # за сколько кампания готова покупать заявку.
    # Klyuchi tseley v snimke (JSON) — stroki, iz API — chisla: privodim k int,
    # inache odnа i ta zhe tsel' popadaet v diff dvazhdy.
    old_prices = {int(k): v for k, v in (before.get("PriorityGoalValues") or {}).items()}
    new_prices = {int(k): v for k, v in (after.get("PriorityGoalValues") or {}).items()}
    for goal_id in sorted(set(old_prices) | set(new_prices), key=lambda x: int(x)):
        if old_prices.get(goal_id) == new_prices.get(goal_id):
            continue
        micros = before.get("PriorityGoalValuesMicro") or {}
        rollback = None
        if micros:
            rollback = json.dumps({
                "method": "update",
                "params": {"Campaigns": [{"Id": campaign_id, block: {"PriorityGoals": [
                    {"GoalId": int(g), "Value": int(v)} for g, v in micros.items()]}}]},
            }, ensure_ascii=False)
        changes.append({
            "campaign_id": campaign_id,
            "campaign_name": name,
            "field": f"цена цели {goal_id}",
            "before": old_prices.get(goal_id),
            "after": new_prices.get(goal_id),
            "severity": "warning",
            "note": "Изменилась цена заявки в приоритетной цели: именно она "
                    "управляет тем, за сколько кампания покупает результат",
            "rollback": rollback,
        })

    for field, label, severity in (("PriorityGoals", "Приоритетные цели", "warning"),
                                   ("AttributionModel", "Модель атрибуции", "info"),
                                   ("CounterIds", "Счётчики Метрики", "info")):
        old_value, new_value = before.get(field), after.get(field)
        if old_value == new_value:
            continue
        changes.append({
            "campaign_id": campaign_id, "campaign_name": name,
            "field": label, "severity": severity,
            "before": old_value, "after": new_value,
            "note": "Изменилась настройка кампании",
        })
    return changes


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

    changes.extend(_diff_strategy(before, after, campaign_id, name))

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
        "strategy_tracked": bool(current.get("campaigns")) and not current.get("limited"),
        "limitation": current.get("limitation"),
    }

"""
Универсальные отчёты Direct Reports API.

Зачем отдельный модуль: до этого отчёты были зашиты в один инструмент со списком
допустимых полей и единственным типом отчёта. Агенту нужно больше — поисковые
запросы, группы, площадки, матрица конверсий по целям. Здесь лежит один
универсальный запуск и готовые сборщики поверх него.

Все вызовы — только чтение. Ни один инструмент этого модуля не меняет аккаунт.
"""

import logging
import time

import requests

import access

log = logging.getLogger("audit.reports")

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"
MAX_WAIT_SECONDS = 180

NAMED_RANGES = {
    "TODAY": 0, "YESTERDAY": 1, "LAST_3_DAYS": 3, "LAST_5_DAYS": 5,
    "LAST_7_DAYS": 7, "LAST_14_DAYS": 14, "LAST_30_DAYS": 30,
    "LAST_90_DAYS": 90, "LAST_365_DAYS": 365,
}


def parse_period(period: str):
    """
    Превращаем человекочитаемый период в параметры отчёта.

    Поддерживаем два вида: именованный диапазон (LAST_7_DAYS) и явные даты через
    запятую: "2026-09-14,2026-09-22". Явные даты нужны для сверки с отчётами
    других систем — без них сравнение невозможно.
    """
    period = (period or "").strip()
    if "," in period:
        left, right = [p.strip() for p in period.split(",", 1)]
        return "CUSTOM_DATE", left, right
    if period in NAMED_RANGES:
        return period, None, None
    raise ValueError(f"Неизвестный период: {period!r}")


def previous_period(period: str):
    """
    Предыдущий период той же длины — для сравнения «к прошлой неделе».

    Для LAST_7_DAYS это 7 дней до текущего окна. Для явных дат — такой же
    отрезок непосредственно перед началом.
    """
    from datetime import date, timedelta

    kind, date_from, date_to = parse_period(period)
    if kind == "CUSTOM_DATE":
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    else:
        days = NAMED_RANGES[kind] or 7
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return f"{prev_start.isoformat()},{prev_end.isoformat()}"


def _headers(account):
    headers = dict(access.direct(account).headers)
    headers.update({
        "processingMode": "auto",
        "skipReportHeader": "true",
        "skipColumnHeader": "false",
        "skipReportSummary": "true",
        "returnMoneyInMicros": "false",
    })
    return headers


def _parse_tsv(text: str):
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return [], []
    columns = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) == len(columns):
            rows.append(dict(zip(columns, values)))
    return columns, rows


def run_report(account, report_type: str, field_names: list,
               period: str = "LAST_7_DAYS", filters: list | None = None,
               goals: list | None = None, extra: dict | None = None,
               report_name: str | None = None) -> dict:
    """
    Запускаем один отчёт и возвращаем разобранные строки.

    Возвращает {"columns": [...], "rows": [{...}], "count": N} либо
    {"error": "..."} с понятным текстом. Исключения наружу не выпускаем:
    аудит обязан пережить сбой любого отдельного отчёта.
    """
    if goals and len(goals) > 10:
        return {"error": f"Goals может содержать не более 10 элементов, "
                         f"передано {len(goals)}"}
    try:
        date_range_type, date_from, date_to = parse_period(period)
    except ValueError as e:
        return {"error": str(e)}

    selection = {}
    if filters:
        selection["Filter"] = filters
    if date_range_type == "CUSTOM_DATE":
        selection["DateFrom"] = date_from
        selection["DateTo"] = date_to

    params = {
        "SelectionCriteria": selection,
        "FieldNames": field_names,
        "ReportName": report_name or f"agent_{int(time.time() * 1000)}",
        "ReportType": report_type,
        "DateRangeType": date_range_type,
        "Format": "TSV",
        "IncludeVAT": "YES",
        "IncludeDiscount": "NO",
    }
    # Goals — только на верхнем уровне params (проверено на живом API).
    if goals:
        params["Goals"] = [str(g) for g in goals]
    if extra:
        params.update(extra)

    try:
        headers = _headers(account)
    except access.AccessError as e:
        return {"error": str(e)}

    try:
        response = requests.post(REPORTS_URL, json={"params": params},
                                 headers=headers, timeout=60)
        deadline = time.time() + MAX_WAIT_SECONDS
        while response.status_code in (201, 202) and time.time() < deadline:
            time.sleep(int(response.headers.get("retryIn", "5")))
            response = requests.post(REPORTS_URL, json={"params": params},
                                     headers=headers, timeout=60)
    except requests.exceptions.RequestException as e:
        return {"error": f"Сеть недоступна: {e}"}

    body = response.content.decode("utf-8", "replace")
    if response.status_code != 200:
        return {"error": f"Отчёт не сформирован (HTTP {response.status_code}): "
                         f"{body[:300]}"}

    columns, rows = _parse_tsv(body)
    return {"report_type": report_type, "columns": columns, "rows": rows,
            "count": len(rows)}


def num(value) -> float:
    """Число из ячейки отчёта: неразрывные пробелы, запятая, прочерк, пусто."""
    if value is None:
        return 0.0
    text = (str(value).replace("\xa0", "").replace(" ", "")
            .replace(",", ".").strip())
    if text in ("", "-", "—", "--"):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _campaign_filter(campaign_ids):
    if not campaign_ids:
        return None
    return [{"Field": "CampaignId", "Operator": "IN",
             "Values": [str(c) for c in campaign_ids]}]


# ------------------------------------------------------------ готовые отчёты

def search_queries(account, period: str = "LAST_7_DAYS",
                   campaign_ids: list | None = None) -> dict:
    """Статистика по поисковым запросам: что вводили люди и что это стоило."""
    return run_report(account, "SEARCH_QUERY_PERFORMANCE_REPORT",
                      ["Query", "MatchType", "CampaignId", "CampaignName",
                       "AdGroupId", "AdGroupName", "Impressions", "Clicks",
                       "Cost", "Conversions", "Ctr", "AvgCpc"],
                      period=period, filters=_campaign_filter(campaign_ids))


def adgroup_performance(account, period: str = "LAST_7_DAYS",
                        campaign_ids: list | None = None) -> dict:
    """Отчёт по группам: куда именно уходят деньги внутри кампаний."""
    return run_report(account, "ADGROUP_PERFORMANCE_REPORT",
                      ["CampaignId", "CampaignName", "AdGroupId", "AdGroupName",
                       "Impressions", "Clicks", "Ctr", "Cost", "AvgCpc",
                       "Conversions", "CostPerConversion"],
                      period=period, filters=_campaign_filter(campaign_ids))


def placements(account, period: str = "LAST_7_DAYS",
               campaign_ids: list | None = None) -> dict:
    """Отчёт по площадкам: где показывалась реклама и что это дало."""
    return run_report(account, "CUSTOM_REPORT",
                      ["CampaignId", "CampaignName", "Placement",
                       "ExternalNetworkName", "Impressions", "Clicks", "Ctr",
                       "Cost", "AvgCpc", "Conversions", "CostPerConversion"],
                      period=period, filters=_campaign_filter(campaign_ids))


def goal_matrix(account, period: str, goal_ids: list,
                campaign_ids: list | None = None) -> dict:
    """
    Матрица «кампания × цель»: сколько конверсий дала каждая цель.

    Главный отчёт для поиска мёртвых целей. Это единственный доступный нам
    способ узнать, на какие цели реально учатся кампании: инлайн-стратегия
    кампании через API не читается (поле TextCampaign не поддерживается
    сервисом campaigns), а сервис strategies отдаёт только пакетные стратегии.

    Цели запрашиваем порциями по 10 — жёсткий лимит API.
    """
    goal_ids = [int(g) for g in goal_ids]
    if not goal_ids:
        return {"error": "Не переданы цели"}
    if len(goal_ids) > 10:
        return {"error": f"За один вызов не более 10 целей, передано "
                         f"{len(goal_ids)}"}

    data = run_report(account, "CUSTOM_REPORT",
                      ["CampaignId", "Conversions"], period=period,
                      filters=_campaign_filter(campaign_ids), goals=goal_ids)
    if data.get("error"):
        return data

    by_campaign = {}
    for row in data["rows"]:
        cid = str(row.get("CampaignId"))
        bucket = by_campaign.setdefault(cid, {})
        for key, value in row.items():
            if key.startswith("Conversions_"):
                goal_id = int(key.split("_")[1])
                bucket[goal_id] = num(value)

    dead = [g for g in goal_ids
            if sum(b.get(g, 0) for b in by_campaign.values()) == 0]
    return {"goals": goal_ids, "by_campaign": by_campaign, "dead_goals": dead}


def _goal_columns(row):
    """Vybiraem iz stroki otcheta konversii po kazhdoj tseli."""
    out = {}
    for key, value in row.items():
        if key.startswith("Conversions_") :
            try:
                out[int(key.split("_")[1])] = num(value)
            except (IndexError, ValueError):
                continue
    return out


def _split_goals(goals, size=10):
    goals = sorted({int(g) for g in goals})
    return [goals[i:i + size] for i in range(0, len(goals), size)]


def dimension_goal_aware(account, period: str, fields: list, key_field: str,
                         name_field: str, goal_map: dict,
                         campaign_ids=None) -> dict:
    """
    Otchet po izmereniyu (ploshchadki, gruppy) s uchetom prioritetnyh tselej.

    Zachem otdel'naya funkciya. API prinimaet spisok tselej na ves otchet srazu,
    a u kazhdoj kampanii svoi tseli. Poetomu zaprashivaem obyedinenie tselej
    porciyami po 10: kazhdaya porciya daet svoi kolonki "Conversions_<tsel>",
    a potom dlya kazhdoj kampanii summiruem tol'ko ee sobstvennye tseli.

    Bez etogo shaga my vidim konversii po vsem tselyam schetchika, i sverka daet
    druguyu kartinu: u kampanii s myagkoj tsel'yu "nulevyh" ploshchadok pochti
    ne najdetsya, hotya dlya nee oni est'.
    """
    goals_all = sorted({int(g) for gs in goal_map.values() for g in gs})
    if not goals_all:
        return {"error": "Не заданы приоритетные цели аккаунта"}

    merged = {}
    for chunk in _split_goals(goals_all):
        data = run_report(account, "CUSTOM_REPORT",
                          ["CampaignId", "CampaignName"] + fields +
                          ["Impressions", "Clicks", "Ctr", "Cost", "AvgCpc",
                           "Conversions"],
                          period=period,
                          filters=_campaign_filter(campaign_ids), goals=chunk)
        if data.get("error"):
            return data
        for row in data["rows"]:
            cid = str(row.get("CampaignId"))
            key = (cid, str(row.get(key_field)))
            bucket = merged.get(key)
            if bucket is None:
                # Pervaya porciya: bazovye chislovye znacheniya berem ottuda.
                bucket = {"campaign_id": cid,
                          "campaign_name": row.get("CampaignName"),
                          "name": row.get(name_field),
                          "cost": num(row.get("Cost")),
                          "clicks": num(row.get("Clicks")),
                          "impressions": num(row.get("Impressions")),
                          "goals": {}}
                merged[key] = bucket
            bucket["goals"].update(_goal_columns(row))

    rows = []
    for bucket in merged.values():
        # Klyuchi v otchete - stroki, a v konfiguracii - chisla: sveriaem oba vida.
        cid = bucket["campaign_id"]
        own = (goal_map.get(cid) or goal_map.get(int(cid))
               if str(cid).isdigit() else goal_map.get(cid)) or []
        own = [int(g) for g in own]
        known = [g for g in own if g in bucket["goals"]]
        rows.append({
            "campaign_id": bucket["campaign_id"],
            "campaign_name": bucket["campaign_name"],
            "name": bucket["name"],
            "cost": bucket["cost"],
            "clicks": bucket["clicks"],
            "impressions": bucket["impressions"],
            "target_conversions": (sum(bucket["goals"][g] for g in known)
                                   if own and known else None),
            "all_conversions": None,
            "goals_used": own,
        })
    return {"rows": rows, "count": len(rows), "goals": goals_all}


def placements_goal_aware(account, period: str, goal_map: dict,
                          campaign_ids=None) -> dict:
    """Ploshchadki s konversiyami po prioritetnym tselyam kampanij."""
    return dimension_goal_aware(account, period,
                                ["Placement", "ExternalNetworkName"],
                                "Placement", "Placement", goal_map, campaign_ids)


def adgroups_goal_aware(account, period: str, goal_map: dict,
                        campaign_ids=None) -> dict:
    """Gruppy s konversiyami po prioritetnym tselyam kampanij."""
    return dimension_goal_aware(account, period,
                                ["AdGroupId", "AdGroupName"],
                                "AdGroupId", "AdGroupName", goal_map, campaign_ids)

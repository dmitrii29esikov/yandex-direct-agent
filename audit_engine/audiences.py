# -*- coding: utf-8 -*-
"""Аудитории, ретаргетинг и корректировки ставок: снимок и разница.

Зачем. Правки в аудиториях, сегментах и корректировках не видны ни в отчётах
Директа, ни в обычном снимке кампаний — а именно они сильнее всего меняют
охват. Поэтому читаем три сервиса API отдельно:
  * retargetinglists — условия аудиторий (имя, тип, сколько правил);
  * audiencetargets  — какие аудитории привязаны к кампании и их состояние;
  * bidmodifiers     — корректировки ставок (уровень и тип).

Снимки складываются в data/snapshots/audiences_<аккаунт>_<дата>.json,
разница печатается человеческим языком.
"""
import io
import json
import os
from datetime import datetime

import access

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(ROOT, "data", "snapshots")
API_CHUNK = 10


def _chunks(seq, size=API_CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _post(client, service, params):
    result = client.post(service, "get", params)
    if isinstance(result, dict) and result.get("error"):
        return [], str(result.get("error_text") or result.get("error"))[:200]
    payload = (result or {}).get("result") or {}
    for value in payload.values():
        if isinstance(value, list):
            return value, None
    return [], None


def collect(account, campaign_ids):
    """Собирает ретаргетинг-условия, аудитории и корректировки по кампаниям."""
    client = access.direct(account)
    report = {"campaigns": sorted(campaign_ids), "lists": {}, "audiences": [],
              "corrections": [], "errors": []}

    page = {"Limit": 1000, "Offset": 0}
    while True:
        items, error = _post(client, "retargetinglists",
                             {"SelectionCriteria": {},
                              "FieldNames": ["Id", "Name", "Type", "Rules"],
                              "Page": page})
        if error:
            report["errors"].append(f"retargetinglists: {error}")
            break
        for item in items:
            rules = item.get("Rules") or []
            report["lists"][str(item.get("Id"))] = {
                "name": item.get("Name"),
                "type": item.get("Type"),
                "rules": len(rules) if isinstance(rules, list) else rules,
            }
        if len(items) < page["Limit"] or not items:
            break
        page["Offset"] += len(items)

    for chunk in _chunks(list(campaign_ids)):
        items, error = _post(client, "audiencetargets",
                             {"SelectionCriteria": {"CampaignIds": list(chunk)},
                              "FieldNames": ["Id", "CampaignId", "AdGroupId",
                                             "RetargetingListId", "State",
                                             "ContextBid", "StrategyPriority"],
                              "Page": {"Limit": 10000}})
        if error:
            report["errors"].append(f"audiencetargets: {error}")
            continue
        for item in items:
            report["audiences"].append({
                "campaign_id": item.get("CampaignId"),
                "adgroup_id": item.get("AdGroupId"),
                "list_id": str(item.get("RetargetingListId")),
                "state": item.get("State"),
            })
        items, error = _post(client, "bidmodifiers",
                             {"SelectionCriteria": {"CampaignIds": list(chunk),
                                                    "Levels": ["CAMPAIGN", "AD_GROUP"]},
                              "FieldNames": ["Id", "CampaignId", "AdGroupId",
                                             "Level", "Type"],
                              "Page": {"Limit": 10000}})
        if error:
            report["errors"].append(f"bidmodifiers: {error}")
            continue
        for item in items:
            report["corrections"].append({
                "campaign_id": item.get("CampaignId"),
                "adgroup_id": item.get("AdGroupId"),
                "level": item.get("Level"),
                "type": item.get("Type"),
            })
    return report


def save_snapshot(account, report):
    os.makedirs(SNAP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(SNAP_DIR, f"audiences_{account}_{stamp}.json")
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    return path


def last_snapshot(account, compare_with=None):
    if compare_with:
        path = compare_with if os.path.isabs(compare_with) else \
            os.path.join(SNAP_DIR, compare_with)
        return path if os.path.exists(path) else None
    candidates = sorted(
        (name for name in os.listdir(SNAP_DIR)
         if name.startswith(f"audiences_{account}_") and name.endswith(".json")),
        reverse=True) if os.path.isdir(SNAP_DIR) else []
    return os.path.join(SNAP_DIR, candidates[0]) if candidates else None


def _audience_key(item):
    return f"кампания {item['campaign_id']} → группа {item.get('adgroup_id')} → аудитория {item['list_id']}"


def _correction_key(item):
    return f"кампания {item['campaign_id']} → {item['level']} → {item['type']}"


def diff(previous, current):
    """Что изменилось по аудиториям, условиям и корректировкам."""
    changes = []
    prev_lists, cur_lists = previous.get("lists") or {}, current.get("lists") or {}
    for list_id in set(cur_lists) - set(prev_lists):
        changes.append(f"+ условие ретаргетинга «{cur_lists[list_id]['name']}» "
                       f"(id {list_id}, правил {cur_lists[list_id]['rules']})")
    for list_id in set(prev_lists) - set(cur_lists):
        changes.append(f"− условие ретаргетинга «{prev_lists[list_id]['name']}» "
                       f"(id {list_id})")
    for list_id in set(prev_lists) & set(cur_lists):
        before, after = prev_lists[list_id], cur_lists[list_id]
        if before.get("rules") != after.get("rules"):
            changes.append(f"~ условие «{after.get('name')}» (id {list_id}): "
                           f"правил {before.get('rules')} → {after.get('rules')}")
        if before.get("name") != after.get("name"):
            changes.append(f"~ условие id {list_id}: «{before.get('name')}» → "
                           f"«{after.get('name')}»")

    prev_aud = {_audience_key(x): x for x in (previous.get("audiences") or [])}
    cur_aud = {_audience_key(x): x for x in (current.get("audiences") or [])}
    names = cur_lists if isinstance(cur_lists, dict) else {}
    for key in set(cur_aud) - set(prev_aud):
        item = cur_aud[key]
        name = (names.get(item["list_id"]) or {}).get("name") or item["list_id"]
        changes.append(f"+ аудитория «{name}» подключена: {key} [{item['state']}]")
    for key in set(prev_aud) - set(cur_aud):
        item = prev_aud[key]
        name = (prev_lists.get(item["list_id"]) or {}).get("name") or item["list_id"]
        changes.append(f"− аудитория «{name}» отключена: {key}")

    prev_cor = {_correction_key(x): x for x in (previous.get("corrections") or [])}
    cur_cor = {_correction_key(x): x for x in (current.get("corrections") or [])}
    for key in set(cur_cor) - set(prev_cor):
        changes.append(f"+ корректировка ставок: {key}")
    for key in set(prev_cor) - set(cur_cor):
        changes.append(f"− корректировка ставок: {key}")

    return changes

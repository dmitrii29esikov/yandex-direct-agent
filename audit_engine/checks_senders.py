# -*- coding: utf-8 -*-
"""Кто реально отправляет цель: сам сайт, Тег Менеджер — или никто.

Зачем проверка. В Директе цель можно назначить, но событие в Метрику может
никто не отправлять — тогда конверсий не будет никогда, и по API Директа это
не видно. Обратная ситуация тоже опасна: цель шлёт ТОЛЬКО Тег Менеджер, и
отключение контейнера обнуляет сигнал стратегии (именно так и произошло
24.09.2026, когда контейнеры Кладовкера и Print Yard были остановлены).

Как проверяем — три независимых источника, без догадок:
1. Опубликованная конфигурация контейнера (mc.yandex.ru/ytm-config): какие
   идентификаторы шлют теги и есть ли теги вообще.
2. Код самого сайта: HTML и свои JS, поиск тех же идентификаторов.
3. Цели из API Метрики: тип цели и идентификатор события.

Проверяются только цели типа action — стандартные цели Метрики (форма,
звонок, файл) считает сама Метрика, отправитель им не нужен.

Если код сайта прочитать не удалось, вывод «отправителя нет» НЕ делается:
проверка честно сообщает, что не смогла проверить. Результаты кэшируются
в data/cache на 6 часов.
"""
import io
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests

from .base import register

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "cache")
CACHE_TTL = timedelta(hours=6)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 Chrome/124 Safari/537.36"}
EVENT_RE = re.compile(r"reachGoal\W{0,6}['\"]([^'\"]{2,80})['\"]")
MAX_SCRIPTS = 10


# --------------------------------------------------------------------------- кэш
def _cache_read(key):
    try:
        with io.open(os.path.join(CACHE_DIR, key + ".json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        when = datetime.fromisoformat(payload.get("_ts", ""))
        if datetime.now() - when < CACHE_TTL:
            return payload.get("data")
    except Exception:                                        # noqa: BLE001
        return None
    return None


def _cache_write(key, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with io.open(os.path.join(CACHE_DIR, key + ".json"), "w",
                     encoding="utf-8") as fh:
            json.dump({"_ts": datetime.now().isoformat(), "data": data}, fh,
                      ensure_ascii=False)
    except Exception:                                        # noqa: BLE001
        pass


def _get(url, timeout=25):
    session = requests.Session()
    session.headers.update(HEADERS)
    return session.get(url, timeout=timeout)


# --------------------------------------------------------- отправители событий
def ytm_events(counter_id):
    """{'container', 'version', 'tags_total', 'events': {ident: [tag ids]}, 'error'}."""
    key = f"ytm_events_{counter_id}"
    cached = _cache_read(key)
    if cached is not None:
        return cached
    data = {"container": None, "version": None, "tags_total": 0,
            "events": {}, "error": None}
    try:
        config = _get(f"https://mc.yandex.ru/ytm-config/{counter_id}").json()
        data["container"] = config.get("containerId")
        data["version"] = config.get("containerVersion")
        tags = config.get("tags") or []
        data["tags_total"] = len(tags)
        for tag in tags:
            for value in (tag.get("data") or {}).values():
                if isinstance(value, str):
                    text = value
                elif isinstance(value, list):
                    text = "\n".join(str(item) for item in value)
                else:
                    continue
                for identifier in EVENT_RE.findall(text):
                    data["events"].setdefault(identifier, []).append(tag.get("id"))
    except Exception as exc:                                 # noqa: BLE001
        data["error"] = f"{type(exc).__name__}: {exc}"[:140]
    _cache_write(key, data)
    return data


def site_text(counter_id, site):
    """HTML и свои JS сайта одним текстом. Пусто = прочитать не удалось."""
    key = f"site_code_{counter_id}"
    cached = _cache_read(key)
    if cached is not None:
        return cached
    payload = {"site": site, "text": "", "scripts": 0, "error": None}
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        root = site if site.startswith("http") else "https://" + site.rstrip("/") + "/"
        html = session.get(root, timeout=20).content.decode("utf-8", "replace")
        payload["text"] = html
        host = urlparse(root).netloc
        sources = []
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html):
            url = urljoin(root, src)
            if urlparse(url).netloc == host and url not in sources:
                sources.append(url)
        for url in sources[:MAX_SCRIPTS]:
            try:
                payload["text"] += "\n" + session.get(
                    url, timeout=25).content.decode("utf-8", "replace")
                payload["scripts"] += 1
            except Exception:                                # noqa: BLE001
                continue
    except Exception as exc:                                 # noqa: BLE001
        payload["error"] = f"{type(exc).__name__}: {exc}"[:140]
    _cache_write(key, payload)
    return payload


def metrica_goals(account, counter_id):
    """{id цели: {'identifier', 'type', 'name'}}."""
    key = f"metrica_goals_{counter_id}"
    cached = _cache_read(key)
    if cached is not None:
        return cached
    result = {}
    try:
        import access                                        # noqa: PLC0415
        payload = access.metrica_get(
            account, f"/management/v1/counter/{counter_id}/goals")
        for goal in payload.get("goals") or []:
            identifier = ""
            for condition in goal.get("conditions") or []:
                if isinstance(condition, dict):
                    identifier = (condition.get("url")
                                  or condition.get("value") or identifier)
            result[str(goal.get("id"))] = {"identifier": identifier or "",
                                           "type": goal.get("type"),
                                           "name": goal.get("name")}
    except Exception as exc:                                 # noqa: BLE001
        result["_error"] = f"{type(exc).__name__}: {exc}"[:120]
    _cache_write(key, result)
    return result


def metrica_sites(account):
    """{id счётчика: домен сайта} — site2 в API это объект, не строка."""
    key = "metrica_sites"
    cached = _cache_read(key)
    if cached is not None:
        return cached
    result = {}
    try:
        import access                                        # noqa: PLC0415
        payload = access.metrica_get(account, "/management/v1/counters")
        for counter in payload.get("counters") or []:
            site = counter.get("site2")
            if isinstance(site, dict):
                site = site.get("site") or site.get("domain") or ""
            site = (site or counter.get("site") or "").strip()
            if site:
                result[str(counter.get("id"))] = site
    except Exception:                                        # noqa: BLE001
        pass
    _cache_write(key, result)
    return result


def metrica_reaches(account, counter_id, goal_ids, days=30):
    """Достижения целей за период: {goal_id: число}. Кэш 6 часов."""
    key = f"metrica_reaches_{counter_id}_{days}"
    cached = _cache_read(key)
    result = dict(cached) if isinstance(cached, dict) else {}
    missing = [g for g in goal_ids if str(g) not in result]
    if not missing:
        return result
    try:
        import access                                        # noqa: PLC0415
        headers = access.metrica_headers(account)
        metrics = ",".join(f"ym:s:goal{g}reaches" for g in missing[:20])
        response = requests.get(
            f"{access.METRICA_BASE}/stat/v1/data", headers=headers,
            params={"ids": counter_id, "metrics": metrics,
                    "date1": f"{days}daysAgo", "date2": "today",
                    "accuracy": "full"}, timeout=60)
        if response.status_code == 200:
            totals = response.json().get("totals") or []
            for goal, value in zip(missing, totals):
                result[str(goal)] = int(value or 0)
            _cache_write(key, result)
    except Exception:                                        # noqa: BLE001
        pass
    return result


# ------------------------------------------------------------------ проверка
def _campaign_goal_ids(info):
    """Цели, на которых реально работает кампания: приоритетные, иначе цели
    каналов из стратегии."""
    priority = [g for g in (info.get("priority_goals") or []) if g]
    if priority:
        return priority
    goals = []
    for scope in (info.get("scopes") or {}).values():
        if isinstance(scope, dict):
            for goal in (scope.get("goals") or []):
                if goal and goal not in goals:
                    goals.append(goal)
    return goals or [g for g in (info.get("goals_all") or []) if g]


def _strategy_info(strategies, campaign_id):
    if not isinstance(strategies, dict):
        return {}
    info = strategies.get(campaign_id)
    if info is None:
        info = strategies.get(str(campaign_id))
    return info if isinstance(info, dict) else {}


def _campaign_meta(campaign):
    return {"object_type": "campaign",
            "object_id": str(campaign.get("Id") or ""),
            "object_name": campaign.get("Name") or ""}


@register("CROSS.GOAL_SENDER", "cross", "warning",
          description="Кто отправляет цель: сайт, Тег Менеджер или никто")
def check_goal_senders(ctx):
    campaigns = [c for c in (ctx.data.get("campaigns") or [])
                 if c.get("State") == "ON"]
    strategies = ctx.data.get("strategies") or {}
    account = getattr(ctx, "account", None)
    if not campaigns or not strategies or not account:
        return []

    sites = metrica_sites(account)
    findings = []
    orphans = {}
    stopped = {}

    for campaign in campaigns:
        info = _strategy_info(strategies, campaign.get("Id"))
        counters = [str(c) for c in (info.get("counter_ids") or [])]
        goals = _campaign_goal_ids(info)
        if not counters or not goals:
            continue

        ytm_here, site_parts, site_readable = {}, [], False
        for counter in counters:
            ytm = ytm_events(counter)
            ytm_here.update(ytm.get("events") or {})
            container_id = str(ytm.get("container") or "")
            if container_id.isdigit() and int(container_id) > 1000 \
                    and not ytm.get("tags_total") and not ytm.get("error"):
                stopped.setdefault(str(ytm["container"]), []).append(
                    campaign.get("Name"))
            site = sites.get(counter)
            if site:
                payload = site_text(counter, site)
                if payload.get("text"):
                    site_readable = True
                    site_parts.append(payload["text"])

        site_blob = "\n".join(site_parts)
        goal_map = {}
        for counter in counters:
            goal_map.update(metrica_goals(account, counter))
        known = {m.get("identifier") for m in goal_map.values()
                 if m.get("identifier")}

        nowhere, only_ytm, both = [], [], []
        for goal_id in goals:
            meta = goal_map.get(str(goal_id))
            if not meta or meta.get("type") != "action" or not meta.get("identifier"):
                continue
            identifier = meta["identifier"]
            in_ytm = identifier in ytm_here
            in_site = bool(site_blob) and identifier in site_blob
            if in_ytm and in_site:
                both.append(f"{identifier} (цель {goal_id})")
            elif in_ytm:
                only_ytm.append(f"{identifier} (цель {goal_id})")
            elif not in_site and site_readable:
                nowhere.append((goal_id, identifier))

        meta_info = _campaign_meta(campaign)
        if nowhere:
            reaches = {}
            for counter in counters:
                reaches.update(metrica_reaches(
                    account, counter, [goal for goal, _ in nowhere]))
            dead, unclear = [], []
            for goal_id, identifier in nowhere:
                text = f"{identifier} (цель {goal_id})"
                (unclear if int(reaches.get(str(goal_id), 0)) > 0 else dead).append(text)
            if dead:
                findings.append(dict(
                    title="Цель назначена, но событие никто не отправляет",
                    detail="Вызова с этим идентификатором нет ни в коде сайта, "
                           "ни в конфигурации ТМ, и по цели НЕТ достижений за "
                           "30 дней. Конверсий не будет.",
                    evidence={"цели без отправителя": "; ".join(dead[:6]),
                              "проверено": f"контейнеры {', '.join(counters)}, "
                                           f"код сайта {len(site_parts)} источник(ов), "
                                           f"достижения за 30 дней: 0"},
                    fix="Вернуть отправку на сайте или в теге ТМ",
                    severity="error", **meta_info))
            if unclear:
                findings.append(dict(
                    title="Отправитель цели вне проверенного кода",
                    detail="Вызова в HTML и подключённых скриптах нет, но цель "
                           "достигается — значит событие шлёт код, который не "
                           "виден при чтении страницы (ленивый JS, сторонний "
                           "сервис). Цель работает, отправитель не подтверждён.",
                    evidence={"цель работает, отправитель не найден":
                              "; ".join(unclear[:6])},
                    fix="Уточнить отправителя в интерфейсе Метрики",
                    severity="info", **meta_info))
        if only_ytm:
            findings.append(dict(
                title="Цель отправляет только Тег Менеджер",
                detail="Событие приходит исключительно из контейнера: снятие "
                       "тега с публикации или остановка контейнера отключают цель.",
                evidence={"цели только из ТМ": "; ".join(only_ytm[:6])},
                fix="Продублировать отправку на сайте", **meta_info))
        if both:
            findings.append(dict(
                title="Цель отправляют и сайт, и Тег Менеджер",
                detail="Одно событие уходит из двух мест: в отчётах это дубль, "
                       "а при разных условиях отправки — расхождение цифр.",
                evidence={"дублирующие отправители": "; ".join(both[:6])},
                fix="Оставить один источник отправки",
                severity="info", **meta_info))

        for identifier in ytm_here:
            if identifier not in known:
                orphans.setdefault(identifier, []).append(campaign.get("Name"))

    if orphans:
        listed = "; ".join(f"{ident} → {', '.join(names[:2])}"
                           for ident, names in list(orphans.items())[:5])
        findings.append(dict(
            title="Тег Менеджер шлёт событие без цели в Метрике",
            detail="Событие приходит, но цели с таким идентификатором нет — "
                   "данные уходят в пустоту.",
            evidence={"события без цели": listed},
            fix="Создать цель с этим идентификатором или убрать отправку",
            severity="info", **_campaign_meta(campaigns[0])))

    if stopped:
        listed = "; ".join(f"контейнер {cid} → {', '.join(names[:3])}"
                           for cid, names in stopped.items())
        first = campaigns[0]
        findings.append(dict(
            title="Контейнер Тэг Менеджера не публикует ни одного тега",
            detail="Опубликованная конфигурация контейнера пустая — значит все "
                   "цели, которые он отправлял, сейчас не работают.",
            evidence={"остановленные контейнеры": listed},
            fix="Проверить, какие цели зависели от контейнера, и вернуть "
                "отправку на сайте",
            **_campaign_meta(first)))

    return findings

"""
Onlajn-audit po API: bez vygruzok Direct Commander.

Otlичае от tools/audit_files.py: tot chitaet XLSX, a etot — sam akkaunt.
"""

from mcp_instance import mcp

import accounts_store as store
from audit_engine import CHECKS, FIXERS, rollback, run_apply, run_audit


@mcp.tool()
def audit_account(account: str | None = None,
                  date_range: str = "LAST_30_DAYS",
                  top: int = 10,
                  save_files: bool = True,
                  only_active: bool = False,
                  deep: bool = False,
                  include_archived: bool = False) -> dict:
    """
    Полный аудит аккаунта по API: Директ + Метрика + Tag Manager.

    Возвращает диагнозы (что исправлять и в каком порядке), находки по типам,
    список непроверенного и markdown-отчёт. Дополнительно сохраняет XLSX
    и чек-лист правок в data/audits/.

    Инструмент работает ТОЛЬКО НА ЧТЕНИЕ.

    :param account: имя аккаунта или любой знакомый ID (счётчик, контейнер, логин)
    :param date_range: период статистики (LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, ...)
    :param top: сколько диагнозов вернуть
    :param save_files: сохранять ли XLSX и remediation.md
    :param only_active: True — только запущенные кампании (State = ON),
                        пауза тоже не попадёт в отчёт
    :param include_archived: True — включить архивные кампании. По умолчанию
                        архив НЕ аудируется: он не тратит бюджет, а прогон
                        удлиняет и добавляет шум («мёртвые» цели и группы)
    :param deep: True — добавить тяжёлые отчёты: сравнение периодов с прошлым,
                 аномалии, поисковые запросы, площадки и группы без целевых
                 конверсий, цели без срабатываний. Прогон становится дольше
                 на несколько минут, зато аудит выходит на уровень ручного
    """
    return run_audit(account=account, date_range=date_range, top=top,
                     save_files=save_files, only_active=only_active, deep=deep,
                     include_archived=include_archived)


@mcp.tool()
def audit_all_accounts(date_range: str = "LAST_30_DAYS", top: int = 5) -> dict:
    """
    Аудит всех аккаунтов реестра по очереди.

    Один «отвалившийся» клиент не ломает прогон остальных: ошибка попадает
    в результат этого аккаунта и работа продолжается.
    """
    results = []
    for name in store.account_ids():
        try:
            res = run_audit(account=name, date_range=date_range, top=top,
                            save_files=False)
        except Exception as e:
            res = {"account": name, "error": f"{type(e).__name__}: {e}"}
        results.append({
            "account": name,
            "error": res.get("error"),
            "findings_total": res.get("findings_total"),
            "errors": res.get("errors"),
            "top_diagnosis": res.get("top_diagnosis"),
            "not_checked": len(res.get("not_checked") or []),
        })
    ok = [r for r in results if not r.get("error")]
    return {
        "accounts": len(results),
        "ok": len(ok),
        "failed": len(results) - len(ok),
        "results": results,
    }


@mcp.tool()
def list_audit_checks() -> dict:
    """Список всех проверок аудита: код, контур, уровень, что ищет и как правится."""
    return {
        "count": len(CHECKS),
        "checks": [
            {"code": c.code, "contour": c.contour, "severity": c.severity,
             "fixable": c.fixable, "description": c.description}
            for c in CHECKS
        ],
    }


# --------------------------------------------------------------------------
# Pravki (rezhim apply s dry-run)
# --------------------------------------------------------------------------

@mcp.tool()
def list_audit_fixers() -> dict:
    """Какие правки агент умеет применять сам, а какие только вручную."""
    return {
        "count": len(FIXERS),
        "fixers": [{"code": fx["code"], "description": fx["description"]}
                   for fx in FIXERS],
        "note": "Всё остальное правится вручную или в интерфейсе Tag Manager (ytm_ui).",
    }


@mcp.tool()
def apply_audit_fixes(account: str | None = None,
                      date_range: str = "LAST_30_DAYS",
                      confirm: bool = False,
                      codes: list[str] | None = None) -> dict:
    """
    Применение автоматических правок с обязательным dry-run.

    Без confirm=True НИЧЕГО не изменяется: инструмент только строит план
    (что именно и на что будет изменено). С confirm=True применяет изменения,
    пишет журнал в data/audits/apply_*.json и возвращает подсказку по откату.

    :param account: имя аккаунта или любой знакомый ID
    :param date_range: период для сбора статистики
    :param confirm: False — план (dry-run), True — применить
    :param codes: ограничить набор кодов (например ['DIRECT.TEXT_LENGTH'])
    """
    return run_apply(account=account, date_range=date_range,
                     confirm=confirm, codes=codes)


@mcp.tool()
def rollback_apply(log_file: str, confirm: bool = False) -> dict:
    """
    Откат применённых правок по журналу.

    Без confirm=True показывает, что именно будет откачено и что откатить
    нельзя (например, снятие дневного бюджета через API невозможно).

    :param log_file: имя файла журнала из data/audits/ или полный путь
    :param confirm: False — план отката, True — выполнить
    """
    return rollback(log_file=log_file, confirm=confirm)


# ------------------------------------------------------------------ отчёты

@mcp.tool()
def get_report(account: str | None = None,
               report_type: str = "CUSTOM_REPORT",
               field_names: list[str] | None = None,
               period: str = "LAST_7_DAYS",
               filter_field: str | None = None,
               filter_values: list[str] | None = None,
               goals: list[int] | None = None) -> dict:
    """
    Произвольный отчёт Direct Reports API.

    :param report_type: SEARCH_QUERY_PERFORMANCE_REPORT,
        ADGROUP_PERFORMANCE_REPORT, CRITERIA_PERFORMANCE_REPORT,
        CAMPAIGN_PERFORMANCE_REPORT, CUSTOM_REPORT
    :param field_names: список полей отчёта
    :param period: LAST_7_DAYS, LAST_30_DAYS либо явные даты "2026-09-14,2026-09-20"
    :param filter_field: поле фильтра, например CampaignId
    :param filter_values: значения фильтра
    :param goals: до 10 идентификаторов целей Метрики. Указание целей меняет
        смысл колонки Conversions: без них это конверсии по всем целям счётчика
    """
    from audit_engine import direct_reports as dr

    if not field_names:
        return {"error": "Не переданы field_names"}
    filters = None
    if filter_field and filter_values:
        filters = [{"Field": filter_field, "Operator": "IN",
                    "Values": [str(v) for v in filter_values]}]
    return dr.run_report(account, report_type, field_names, period=period,
                         filters=filters, goals=goals)


@mcp.tool()
def search_queries_report(account: str | None = None,
                          period: str = "LAST_7_DAYS",
                          limit: int = 50,
                          without_conversions_only: bool = True) -> dict:
    """
    Поисковые запросы: что вводили люди, сколько это стоило и что дало.

    :param without_conversions_only: True — только запросы, которые получили
        клики и не дали конверсий. Это кандидаты в минус-фразы.
    """
    from audit_engine import direct_reports as dr

    data = dr.search_queries(account, period)
    if data.get("error"):
        return data
    rows = data.get("rows") or []
    prepared = [{
        "запрос": r.get("Query"), "тип": r.get("MatchType"),
        "кампания": r.get("CampaignName"), "группа": r.get("AdGroupName"),
        "показы": dr.num(r.get("Impressions")), "клики": dr.num(r.get("Clicks")),
        "расход ₽": round(dr.num(r.get("Cost")), 2),
        "конверсии": dr.num(r.get("Conversions")),
        "цена клика ₽": round(dr.num(r.get("AvgCpc")), 2),
    } for r in rows]
    with_clicks = [r for r in prepared if r["клики"] > 0]
    wasted = [r for r in with_clicks if r["конверсии"] == 0]
    shown = wasted if without_conversions_only else with_clicks
    shown.sort(key=lambda r: -r["расход ₽"])
    return {
        "period": period,
        "всего строк": len(prepared),
        "запросов с кликами": len(with_clicks),
        "без конверсий": len(wasted),
        "расход на запросы без конверсий ₽":
            round(sum(r["расход ₽"] for r in wasted), 2),
        "показано": min(limit, len(shown)),
        "rows": shown[:limit],
    }


@mcp.tool()
def placements_report(account: str | None = None,
                      period: str = "LAST_7_DAYS",
                      limit: int = 50) -> dict:
    """
    Площадки: где показывалась реклама, сколько стоила и что дала.

    Показывает агрегат расхода по площадкам без целевых конверсий — деньги
    обычно растекаются по длинному хвосту, поэтому агрегат важнее отдельных
    строк. Отдельно даётся вклад от размещений на площадках Яндекса.
    """
    from audit_engine import direct_reports as dr
    import access

    goal_map = access.priority_goals(account)
    if goal_map:
        data = dr.placements_goal_aware(account, period, goal_map)
        if data.get("error"):
            return data
        rows = data.get("rows") or []
        prepared = [{
            "площадка": r.get("name"), "кампания": r.get("campaign_name"),
            "расход ₽": round(r.get("cost") or 0, 2),
            "клики": round(r.get("clicks") or 0),
            "целевые": r.get("target_conversions"),
        } for r in rows]
        metric = "целевые"
    else:
        data = dr.placements(account, period)
        if data.get("error"):
            return data
        prepared = [{
            "площадка": r.get("Placement"),
            "площадка Яндекса": r.get("ExternalNetworkName"),
            "кампания": r.get("CampaignName"),
            "расход ₽": round(dr.num(r.get("Cost")), 2),
            "клики": dr.num(r.get("Clicks")),
            "конверсии": dr.num(r.get("Conversions")),
        } for r in data.get("rows") or []]
        metric = "конверсии (все цели)"

    total = sum(r["расход ₽"] for r in prepared)
    zero = [r for r in prepared if r.get("расход ₽", 0) > 0
            and (r.get(metric) in (0, None))]
    zero.sort(key=lambda r: -r["расход ₽"])
    prepared.sort(key=lambda r: -r["расход ₽"])
    return {
        "period": period, "метрика": metric,
        "площадок": len(prepared), "весь расход ₽": round(total, 2),
        "площадок без результата": len(zero),
        "расход без результата ₽": round(sum(r["расход ₽"] for r in zero), 2),
        "доля %": round(sum(r["расход ₽"] for r in zero) / total * 100, 1) if total else 0,
        "top_by_spend": prepared[:limit],
        "without_result": zero[:limit],
    }


@mcp.tool()
def adgroups_report(account: str | None = None,
                    period: str = "LAST_7_DAYS",
                    limit: int = 100) -> dict:
    """Группы объявлений: расход, клики, целевые и сумма по группам без результата."""
    from audit_engine import direct_reports as dr
    import access

    goal_map = access.priority_goals(account)
    if goal_map:
        data = dr.adgroups_goal_aware(account, period, goal_map)
        if data.get("error"):
            return data
        prepared = [{
            "группа": r.get("name"), "кампания": r.get("campaign_name"),
            "расход ₽": round(r.get("cost") or 0, 2),
            "клики": round(r.get("clicks") or 0),
            "целевые": r.get("target_conversions"),
        } for r in data.get("rows") or []]
        metric = "целевые"
    else:
        data = dr.adgroup_performance(account, period)
        if data.get("error"):
            return data
        prepared = [{
            "группа": r.get("AdGroupName"), "кампания": r.get("CampaignName"),
            "расход ₽": round(dr.num(r.get("Cost")), 2),
            "клики": dr.num(r.get("Clicks")),
            "конверсии": dr.num(r.get("Conversions")),
        } for r in data.get("rows") or []]
        metric = "конверсии (все цели)"

    total = sum(r["расход ₽"] for r in prepared)
    zero = [r for r in prepared if r.get("расход ₽", 0) > 0
            and (r.get(metric) in (0, None))]
    prepared.sort(key=lambda r: -r["расход ₽"])
    zero.sort(key=lambda r: -r["расход ₽"])
    return {
        "period": period, "метрика": metric,
        "групп": len(prepared), "весь расход ₽": round(total, 2),
        "групп без результата": len(zero),
        "расход без результата ₽": round(sum(r["расход ₽"] for r in zero), 2),
        "доля %": round(sum(r["расход ₽"] for r in zero) / total * 100, 1) if total else 0,
        "top_by_spend": prepared[:limit],
        "without_result": zero[:limit],
    }


@mcp.tool()
def campaign_deltas_report(account: str | None = None,
                           period: str = "LAST_7_DAYS") -> dict:
    """
    Динамика к предыдущему периоду той же длины: расход, клики, цена клика,
    конверсии, цена конверсии — и список аномалий.
    """
    from audit_engine import deltas as dl

    data = dl.campaign_deltas(account, period)
    if data.get("error"):
        return data
    data["markdown"] = dl.render_deltas(data)
    return data


# --------------------------------------------------- изменения конфигурации

@mcp.tool()
def snapshot_campaigns(account: str | None = None,
                       date_range: str | None = None) -> dict:
    """
    Снимок конфигурации кампаний: состояние, бюджет, минус-фразы, таргетинг,
    количество групп и стратегии — тип по каналам, недельный лимит расхода,
    ставки, приоритетные цели, модель атрибуции. Основа для отчёта об изменениях.
    """
    from audit_engine import snapshots

    return snapshots.save_snapshot(account, date_range)


@mcp.tool()
def list_campaign_snapshots(account: str | None = None) -> dict:
    """Список сохранённых снимков конфигурации кампаний, свежие сверху."""
    from audit_engine import snapshots

    return snapshots.list_snapshots(account)


@mcp.tool()
def audit_campaign_changes(account: str | None = None,
                           compare_with: str | None = None) -> dict:
    """
    Что изменилось в настройках кампаний с прошлого снимка.

    Возвращает список изменений и готовые заготовки откатов (JSON для API).
    Заготовки — это текст подсказок: ничего в аккаунте не меняется, пока
    пользователь не даст команду.

    Отслеживаются и стратегии: смена типа стратегии канала, недельный лимит,
    ограничение ставки, целевая цена конверсии, приоритетные цели и модель
    атрибуции.
    """
    from audit_engine import snapshots

    return snapshots.compare_snapshots(account, compare_with)

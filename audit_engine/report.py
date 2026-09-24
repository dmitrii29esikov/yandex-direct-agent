"""
Vyvod rezul'tatov audita: markdown dlya chata, XLSX i chек-list na disk.
"""

import logging
from datetime import datetime

import access
from pathlib import Path

log = logging.getLogger("audit.report")

AUDITS_DIR = Path(__file__).resolve().parent.parent / "data" / "audits"

SEVERITY_LABEL = {"error": "Ошибка", "warning": "Предупреждение", "info": "Инфо"}
FIXABLE_LABEL = {"auto": "правится автоматически", "manual": "правится вручную",
                 "ytm_ui": "только в интерфейсе Tag Manager"}


def _money(value):
    return f"{value:,.0f} ₽".replace(",", " ") if value else "—"


def render_markdown(ctx, findings, diagnoses, top: int = 10) -> str:
    """Otchet dlya cheloveka: snachala vyvody, potom tsifry."""
    lines = []
    title = (getattr(ctx, "record", None) or {}).get("title") or ctx.account
    lines.append(f"# Аудит: {title}")
    lines.append("")
    lines.append(f"Аккаунт: `{ctx.account}` · период: **{ctx.date_range}** · "
                 f"кампаний: **{len(ctx.data.get('campaigns') or [])}** · "
                 f"находок: **{len(findings)}** "
                 f"(ошибок: {sum(1 for f in findings if f.severity == 'error')}, "
                 f"предупреждений: {sum(1 for f in findings if f.severity == 'warning')})")

    units = ctx.data.get("units")
    if units:
        lines.append(f"Баллы Директа: потрачено {units.get('spent')}, "
                     f"остаток {units.get('remaining')} из {units.get('limit')}"
                     + (" ⚠️ остаток меньше 20%" if units.get("warning") else ""))
    lines.append("")

    # --- Diagnozy
    lines.append(f"## Что исправлять (топ-{min(top, len(diagnoses))})")
    lines.append("")
    if not diagnoses:
        lines.append("Критичных проблем не найдено.")
    for i, d in enumerate(diagnoses[:top], 1):
        lines.append(f"### {i}. {d.title} — impact {d.impact}")
        lines.append("")
        lines.append(f"**Почему это важно.** {d.summary}")
        lines.append("")
        lines.append(f"**Последствия.** {d.impact_text}")
        if d.money:
            lines.append("")
            lines.append(f"**Деньги под риском:** {_money(d.money)}")
        lines.append("")
        lines.append("**Порядок действий:**")
        for step in d.actions:
            lines.append(f"1. {step}")
        lines.append("")

    # --- Ekonomika zayavok: kak nastroyeno i vo skolko obhodyatsya zayavki
    economy = render_economy(ctx)
    if economy:
        lines.append(economy)

    # --- Svodka po kodam
    # Dinamika k proshlomu periodu — tol'ko v glubokom rezhime.
    if ctx.data.get("deltas"):
        from . import deltas as _deltas
        lines.append("## Динамика к предыдущему периоду")
        lines.append("")
        lines.append(_deltas.render_deltas(ctx.data["deltas"]))
        lines.append("")

    lines.append("## Находки по типам")
    lines.append("")
    lines.append("| Код | Уровень | Находок | Макс. impact | Как правится |")
    lines.append("|---|---|---|---|---|")
    grouped = {}
    for f in findings:
        grouped.setdefault(f.code, []).append(f)
    for code, items in sorted(grouped.items(),
                              key=lambda kv: max(f.impact for f in kv[1]),
                              reverse=True)[:25]:
        top_item = max(items, key=lambda f: f.impact)
        lines.append(f"| `{code}` | {SEVERITY_LABEL.get(top_item.severity)} | "
                     f"{len(items)} | {top_item.impact} | "
                     f"{FIXABLE_LABEL.get(top_item.fixable)} |")
    lines.append("")

    # --- Ne provereno
    if ctx.errors:
        lines.append("## Не проверено")
        lines.append("")
        for e in ctx.errors:
            lines.append(f"- `{e['label']}` — {e['error']}")
        lines.append("")
    if ctx.notes:
        lines.append("## Примечания")
        lines.append("")
        for n in ctx.notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)


SCOPE_SHORT = {
    "PAY_FOR_CONVERSION": "оплата за конверсии",
    "PAY_FOR_CONVERSION_MULTIPLE_GOALS": "оплата за конверсии, неск. целей",
    "WB_MAXIMUM_CLICKS": "максимум кликов",
    "WB_MAXIMUM_CONVERSION_RATE": "максимум конверсий",
    "WB_MAXIMUM_CONVERSIONS": "максимум конверсий",
    "AVERAGE_CPC": "средняя цена клика (ручная)",
    "AVERAGE_CPA": "средняя цена конверсии",
    "AVERAGE_CRR": "доля расходов",
    "HIGHEST_POSITION": "наивысшая позиция (ручная)",
    "SERVING_OFF": "выкл.",
    "NETWORK_DEFAULT": "как в поиске",
}


def _scope_label(strategy_info):
    """«поиск / сети» человеческим языком."""
    if not strategy_info:
        return "—"
    scopes = strategy_info.get("scopes") or {}
    parts = []
    for scope in ("Search", "Network"):
        stype = ((scopes.get(scope) or {}).get("type")) or "—"
        parts.append(SCOPE_SHORT.get(stype, stype))
    return " / ".join(parts)


def _verdict(price, target, conversions, cost):
    """Короткий вердикт по экономике кампании."""
    if not cost:
        return "не работала"
    if not conversions:
        return "**нет заявок**"
    if not price:
        return "цена неизвестна"
    if target and price > target * 2:
        return f"**переплата ×{price / target:.1f}**"
    if target and price > target:
        return f"дороже плана ×{price / target:.1f}"
    return "в норме"


def _goal_name(ctx, goal_id) -> str:
    names = ctx.data.get("goal_names") or {}
    return names.get(int(goal_id)) or f"цель {goal_id}"


def render_economy(ctx) -> str:
    """
    Экономика заявок — главный раздел отчёта.

    Отвечает на два вопроса владельца: как настроены кампании и во сколько
    реально обходится заявка. Данные: приоритетные цели кампаний (целевые
    конверсии), стратегии, лимиты и расход.
    """
    stats = ctx.data.get("stats")
    if not stats:
        return ""

    target = ctx.target_cpa
    market = ((ctx.record.get("direct") or {}).get("market_cpa")
              or (ctx.record.get("goals") or {}).get("market_cpa"))

    lines = ["## Экономика заявок", ""]
    aims = []
    if target:
        aims.append(f"план — **{target:.0f} ₽** за заявку")
    if isinstance(market, dict):
        aims.append("рыночная цена задана по кампаниям — колонка «Рынок ₽»")
    elif market:
        aims.append(f"рыночная цена — **{market:.0f} ₽**")
    else:
        aims.append("рыночная цена не задана (добавьте `direct.market_cpa` "
                    "в реестр — тогда отчёт будет сравнивать с рынком)")
    lines.append("Ориентиры: " + "; ".join(aims) + ".")
    lines.append("")

    strategies = ctx.data.get("strategies") or {}
    targeted = ctx.data.get("stats_targeted") or {}
    campaigns = ctx.data.get("campaigns") or []
    if strategies:
        lines.append("| Кампания | Стратегия: поиск / сети | Лимит ₽/нед | День ₽ | "
                     "Расход | Заявки | Цена заявки | План ₽ | Рынок ₽ | Вердикт |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        def camp_market(campaign):
            """Рыночная цена заявки: число на аккаунт либо словарь по кампаниям."""
            if not isinstance(market, dict):
                return market
            cid = str(campaign.get("Id"))
            value = market.get(cid)
            if value is None and cid.isdigit():
                value = market.get(int(cid))
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        for c in campaigns:
            cid = str(c.get("Id"))
            info = strategies.get(int(cid)) if str(cid).isdigit() else None
            base = stats.get(cid) or {}
            tgt = targeted.get(cid) or {}
            cost = base.get("cost") or 0
            conversions = tgt.get("conversions") if tgt else base.get("conversions")
            price = tgt.get("cpa") if tgt else base.get("cpa")
            limit = None
            daily = None
            if info:
                limits = [((info.get("scopes") or {}).get(s) or {}).get("weekly_limit")
                          for s in ("Search", "Network")]
                limits = [x for x in limits if x]
                limit = max(limits) if limits else None
                daily = info.get("daily_budget")
            camp_market_value = camp_market(c)
            reference = camp_market_value or target
            lines.append(
                f"| {(c.get('Name') or '')[:38]} | {_scope_label(info)} | "
                f"{_fnum(limit)} | {_fnum(daily)} | {_fnum(cost)} | "
                f"{(conversions or 0):.0f} | {_fnum(price)} | {_fnum(target)} | "
                f"{_fnum(camp_market_value)} | "
                f"{_verdict(price, reference, conversions, cost)} |")
        lines.append("")
        # Kakie tseli uchastvuyut v zakupke, a kakie — tol'ko analitika.
        direct_ids = ctx.data.get("direct_goal_ids") or []
        if not direct_ids:
            resolved = ctx.data.get("priority_goals") or {}
            direct_ids = sorted({int(g) for gs in (resolved.get("api") or {}).values()
                                 for g in gs})
        analytics_ids = ctx.data.get("analytics_goal_ids") or []
        if direct_ids:
            goal_names = ctx.data.get("goal_names") or {}
            shown = "; ".join(access.label_goals(goal_names, direct_ids[:8]))
            lines.append(f"**Цели, по которым Директ покупает результат "
                         f"({len(direct_ids)}):** {shown}.")
            if analytics_ids:
                lines.append(f"Остальные {len(analytics_ids)} целей счётчика — аналитика "
                             f"Метрики: в закупке и обучении алгоритмов не участвуют, "
                             f"поэтому в расчётах не используются.")
            lines.append("")
        lines.append("Заявки считаются по **целям кампаний** — целям стратегий и "
                     "приоритетным целям, то есть по обращениям, а не по "
                     "микродействиям счётчика.")
        lines.append("")

    # Разбивка по целям — есть только в глубоком режиме.
    matrix = ctx.data.get("goal_matrix")
    if matrix and matrix.get("by_campaign"):
        lines.append("### По целям: за что именно платим")
        lines.append("")
        lines.append("| Кампания | Цель | Конверсий | Цена за цель |")
        lines.append("|---|---|---|---|")
        # Tol'ko sobstvennye tseli kampanii: Direct umeet privodit
        # konversii po tselyam chuzhih schetchikov (nablyudali na tseli 13).
        own_goals = {}
        for key, values in ((ctx.data.get("priority_goals") or {}).get("goals") or {}).items():
            own_goals[str(key)] = {int(g) for g in values}
        rows = []
        for cid, bucket in matrix["by_campaign"].items():
            campaign = next((c for c in campaigns if str(c.get("Id")) == str(cid)), {})
            cost = (stats.get(str(cid)) or {}).get("cost") or 0
            for goal_id, count in bucket.items():
                if own_goals.get(str(cid)) and int(goal_id) not in own_goals[str(cid)]:
                    continue
                if not count:
                    continue
                rows.append((count, campaign.get("Name") or str(cid),
                             _goal_name(ctx, goal_id), cost / count))
        rows.sort(key=lambda r: -r[0])
        for count, name, goal_name, price in rows[:15]:
            lines.append(f"| {name[:34]} | {goal_name[:44]} | {count:.0f} | {_fnum(price)} |")
        lines.append("")
        dead = matrix.get("dead_goals") or []
        if dead:
            lines.append("Цели без единого срабатывания за период: "
                         + ", ".join(f"{_goal_name(ctx, g)} ({g})" for g in dead[:10]))
            lines.append("")

    return "\n".join(lines)


def _fnum(value):
    """Число для таблицы: 3 614 вместо 3614.04, и «—» вместо пустоты."""
    if value is None:
        return "—"
    return f"{float(value):,.0f}".replace(",", " ")


def render_remediation(ctx, diagnoses) -> str:
    """Chек-list pravok v vide cheklista."""
    lines = [f"# План правок: {ctx.account}", ""]
    for i, d in enumerate(diagnoses, 1):
        lines.append(f"## {i}. {d.title} (impact {d.impact})")
        for step in d.actions:
            lines.append(f"- [ ] {step}")
        lines.append("")
    return "\n".join(lines)


def save_xlsx(ctx, findings, diagnoses, failures) -> str:
    """XLSX s listami Diagnozy / Nahodki / Ne provereno."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = AUDITS_DIR / f"audit_{ctx.account}_{stamp}.xlsx"

    wb = Workbook()

    ws = wb.active
    ws.title = "Диагнозы"
    ws.append(["Impact", "Диагноз", "Контур", "Резюме", "Последствия", "Действия"])
    for d in diagnoses:
        ws.append([d.impact, d.title, d.contour, d.summary, d.impact_text,
                   "\n".join(f"{i}. {a}" for i, a in enumerate(d.actions, 1))])

    ws2 = wb.create_sheet("Находки")
    ws2.append(["Impact", "Уровень", "Код", "Заголовок", "Детали", "Объект",
                "ID объекта", "Исправление", "Как правится", "Деньги"])
    for f in findings:
        ws2.append([f.impact, SEVERITY_LABEL.get(f.severity), f.code, f.title,
                    f.detail, f.object_type, f.object_id, f.fix,
                    FIXABLE_LABEL.get(f.fixable), f.money])

    ws3 = wb.create_sheet("Не проверено")
    ws3.append(["Что", "Ошибка"])
    for e in ctx.errors:
        ws3.append([e["label"], e["error"]])
    for fl in failures:
        ws3.append([f"проверка {fl['check']}", fl["error"]])

    ws4 = wb.create_sheet("Связка")
    ws4.append(["Параметр", "Значение"])
    ws4.append(["Аккаунт", ctx.account])
    ws4.append(["Период", ctx.date_range])
    ws4.append(["Режим Директа", ctx.data.get("direct_mode")])
    ws4.append(["Счётчики Метрики", ", ".join(str(c.get("id")) for c in (ctx.data.get("counters") or []))])
    ws4.append(["Контейнеры YTM", ", ".join(str(c) for c in (ctx.data.get("ytm") or {}))])
    ws4.append(["Целевой CPA", ctx.target_cpa])
    for e in ctx.data.get("direct_clients") or []:
        ws4.append(["Клиент Директа", f"{e.get('name')} (chief_login: {e.get('chief_login')})"])

    fill = PatternFill("solid", fgColor="DDEBF7")
    for sheet in wb.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center")
        sheet.freeze_panes = "A2"
        for column in sheet.columns:
            width = max((len(str(c.value)) for c in column if c.value is not None), default=10)
            sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)

    wb.save(path)
    return str(path)


def save_remediation(ctx, diagnoses) -> str:
    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = AUDITS_DIR / f"remediation_{ctx.account}_{stamp}.md"
    path.write_text(render_remediation(ctx, diagnoses), encoding="utf-8")
    return str(path)

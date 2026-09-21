"""
Vyvod rezul'tatov audita: markdown dlya chata, XLSX i chек-list na disk.
"""

import logging
from datetime import datetime
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
    lines.append(f"# Аудит аккаунта `{ctx.account}`")
    lines.append("")
    lines.append(f"Период статистики: **{ctx.date_range}** · "
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

    # --- Svodka po kodam
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

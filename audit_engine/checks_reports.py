"""
Проверки, построенные на дополнительных отчётах.

Отдельный модуль, а не дополнение к checks_direct, потому что эти проверки
опираются на тяжёлые отчёты (запросы, площадки, группы, матрица целей,
сравнение периодов). Если отчёт не получен, данные равны None и проверка
честно пропускается — вместо того чтобы выдать ложную находку.

Контур у всех — direct, но источник данных другой.
"""

import re
from collections import Counter

from . import direct_reports as dr
from .base import register

MONEY_FLOOR = 300        # меньше 300 ₽ за период не считаем значимым
TOP_EXAMPLES = 15


def _campaign_meta(ctx, cid):
    name = None
    for camp in ctx.data.get("campaigns") or []:
        if str(camp.get("Id")) == str(cid):
            name = camp.get("Name")
            break
    return {"object_type": "campaign", "object_id": str(cid),
            "object_name": name}


# --------------------------------------------------------------- мёртвые цели

@register("DIRECT.DEAD_GOALS", "direct", "warning",
          description="Цели, которые не срабатывают ни разу")
def check_dead_goals(ctx):
    """
    Цель, которая за период не дала ни одной конверсии.

    Зачем: кампания обучается на той цели, что указана в её стратегии. Если эта
    цель не срабатывает, автостратегия не получает сигнала и деньги расходуются
    без обучения. Приоритетные цели кампании через API не читаются, поэтому
    показываем все мёртвые цели аккаунта с прямым предупреждением.
    """
    if not ctx.loaded("goal_matrix"):
        return []

    matrix = ctx.data.get("goal_matrix") or {}
    dead = matrix.get("dead_goals") or []
    if not dead:
        return []

    names = ctx.data.get("goal_names") or {}
    by_campaign = matrix.get("by_campaign") or {}

    examples = []
    for goal_id in dead[:TOP_EXAMPLES]:
        total = sum(b.get(goal_id, 0) for b in by_campaign.values())
        examples.append({"goal_id": goal_id,
                         "название": names.get(goal_id) or names.get(str(goal_id)),
                         "конверсий": total})

    return [dict(
        title="Цели без срабатываний",
        detail=f"За 30 дней не сработала ни одна конверсия по "
               f"{len(dead)} целям из {len(matrix.get('goals') or [])}. "
               f"Часть из них, скорее всего, просто не используется в "
               f"кампаниях. Опасны те, что прописаны в стратегии кампании: "
               f"если цель не срабатывает, автостратегия не получает сигнала "
               f"и расход идёт без обучения. Список целей кампании через API "
               f"не читается — проверьте в интерфейсе.",
        object_type="account", object_id=ctx.account,
        evidence={"всего целей проверено": len(matrix.get("goals") or []),
                  "мёртвых целей": len(dead), "примеры": examples},
        fix="Проверить в интерфейсе, на какую цель настроена кампания. "
            "Если на мёртвую — заменить её или убрать из приоритетных, "
            "либо починить саму цель в Метрике",
        fixable="manual",
    )]


# ------------------------------------------------- проверки площадок и групп

def _zero_rows(rows, cost_key, conv_key, name_key):
    """Stroki s nulevym tselevym rezul'tatom i ih summa."""
    zero = [r for r in rows if (r.get(cost_key) or 0) > 0
            and r.get(conv_key) == 0]
    total = sum(r.get(cost_key) or 0 for r in rows)
    wasted = sum(r.get(cost_key) or 0 for r in zero)
    zero.sort(key=lambda r: -(r.get(cost_key) or 0))
    examples = [{"название": r.get(name_key), "кампания": r.get("campaign_name"),
                 "расход ₽": round(r.get(cost_key) or 0),
                 "клики": round(r.get("clicks") or 0)}
                for r in zero[:TOP_EXAMPLES]]
    return total, wasted, zero, examples


@register("DIRECT.ZERO_CONVERSION_PLACEMENTS", "direct", "warning",
          description="Площадки без целевых конверсий")
def check_zero_conversion_placements(ctx):
    """
    Skol'ko deneg ushlo v ploshchadki, kotorye ne dali ni odnogo celevogo
    deistviya.

    Schitaem AGGREGAT po vsem takim ploshchadkam bez poroga po kazhdoj: den'gi
    rastekayutsya po dlitelnomu hvostu, i porog v 300 rub. na odnu ploshchadku
    skryvaet kartinu. Otdel'no dayutsya krupnejshie.
    """
    if not ctx.loaded("placements_targeted"):
        return []
    rows = ctx.data["placements_targeted"] or []
    if not rows:
        return []
    total, wasted, zero, examples = _zero_rows(rows, "cost",
                                               "target_conversions", "name")
    if not zero or wasted == 0:
        return []
    share = (wasted / total * 100) if total else 0

    return [dict(
        title="Деньги ушли в площадки без целевых конверсий",
        detail=f"{wasted:.0f} ₽ из {total:.0f} ₽ ({share:.0f}% расхода) "
               f"потрачено на {len(zero)} площадок из {len(rows)}, которые не "
               f"дали ни одного целевого действия.",
        object_type="account", object_id=ctx.account,
        evidence={"потрачено впустую ₽": round(wasted),
                  "доля расхода %": round(share),
                  "площадок без целевых": len(zero), "всего площадок": len(rows),
                  "примеры": examples},
        money=wasted,
        fix="Исключить площадки-пожиратели или понизить по ним ставку",
        fixable="manual",
    )]


@register("DIRECT.ZERO_CONVERSION_GROUPS", "direct", "warning",
          description="Группы без целевых конверсий")
def check_zero_conversion_groups(ctx):
    """To же, chto i po ploshchadkam, no po gruppam obyavlenij."""
    if not ctx.loaded("adgroups_targeted"):
        return []
    rows = ctx.data["adgroups_targeted"] or []
    if not rows:
        return []
    total, wasted, zero, examples = _zero_rows(rows, "cost",
                                               "target_conversions", "name")
    if not zero or wasted == 0:
        return []
    share = (wasted / total * 100) if total else 0

    return [dict(
        title="Деньги ушли в группы без целевых конверсий",
        detail=f"{wasted:.0f} ₽ из {total:.0f} ₽ ({share:.0f}% расхода) "
               f"пришлось на {len(zero)} групп из {len(rows)}, которые не дали "
               f"ни одного целевого действия.",
        object_type="account", object_id=ctx.account,
        evidence={"потрачено впустую ₽": round(wasted),
                  "доля расхода %": round(share),
                  "групп без целевых": len(zero), "всего групп": len(rows),
                  "примеры": examples},
        money=wasted,
        fix="Разобрать релевантность этих групп: фразы, объявления, "
            "посадочные страницы",
        fixable="manual",
    )]


# ------------------------------------------------------- поисковые запросы

@register("DIRECT.JUNK_QUERIES", "direct", "warning",
          description="Поисковые запросы, которые тянут деньги без отдачи")
def check_junk_queries(ctx):
    if not ctx.loaded("search_queries"):
        return []
    rows = ctx.data["search_queries"]
    if not rows:
        return []

    paid = [r for r in rows if dr.num(r.get("Clicks")) > 0]
    bad = [r for r in paid if dr.num(r.get("Conversions")) == 0]
    if not bad:
        return []

    wasted = sum(dr.num(r.get("Cost")) for r in bad)
    total = sum(dr.num(r.get("Cost")) for r in paid)
    share = (wasted / total * 100) if total else 0
    bad.sort(key=lambda r: -dr.num(r.get("Cost")))
    examples = [{"запрос": r.get("Query"), "клики": dr.num(r.get("Clicks")),
                 "расход": dr.num(r.get("Cost")),
                 "кампания": r.get("CampaignName")}
                for r in bad[:TOP_EXAMPLES]]

    return [dict(
        title="Поисковые запросы без конверсий",
        detail=f"{len(bad)} запросов из {len(paid)} получили клики и не дали "
               f"конверсий: {wasted:.0f} ₽ ({share:.0f}% расхода с поиска). "
               f"Это кандидаты в минус-фразы.",
        object_type="account", object_id=ctx.account,
        evidence={"потрачено ₽": round(wasted), "доля расхода с поиска %": round(share),
                  "запросов с кликами": len(paid), "без конверсий": len(bad),
                  "примеры": examples},
        money=wasted,
        fix="Добавить мусорные запросы в минус-фразы на уровне кампании",
        fixable="manual",
    )]


# ------------------------------------------- расхождения минус-фраз по проектам

def _project_key(name: str) -> str:
    """Грубый ключ проекта по названию кампании: первое значимое слово."""
    text = re.sub(r"[^А-Яа-яЁёA-Za-z0-9 ]", " ", str(name or "")).lower()
    for word in text.split():
        if word not in ("епк", "рся", "пойск", "поиск", "сеть", "копия", "от", "и",
                        "new", "test", "тест"):
            return word
    return text.strip() or "прочее"


@register("DIRECT.MINUS_PHRASES_MISMATCH", "direct", "info",
          description="Разные минус-фразы у кампаний одного проекта")
def check_minus_phrases_mismatch(ctx):
    """
    Сравниваем минус-фразы кампаний, которые относятся к одному проекту.

    Проект определяется грубо — по первому значимому слову названия. Если
    кампании одного бренда защищены по-разному, часть из них сливает трафик,
    который соседняя уже отсекла.
    """
    if not ctx.loaded("campaigns"):
        return []

    groups = {}
    for camp in ctx.data.get("campaigns") or []:
        negatives = set()
        raw = camp.get("NegativeKeywords")
        if isinstance(raw, dict):
            negatives = set(raw.get("Items") or [])
        elif isinstance(raw, list):
            negatives = set(raw)
        if not negatives:
            continue
        groups.setdefault(_project_key(camp.get("Name")), []).append(
            {"id": camp.get("Id"), "name": camp.get("Name"),
             "negatives": negatives})

    out = []
    for project, camps in groups.items():
        if len(camps) < 2:
            continue
        union = set()
        for camp in camps:
            union |= camp["negatives"]
        gap = {}
        for camp in camps:
            missing = sorted(union - camp["negatives"])
            if missing:
                gap[camp["name"]] = missing[:25]
        if gap:
            out.append(dict(
                title=f"Минус-фразы различаются внутри проекта «{project}»",
                detail=f"У {len(gap)} кампаний из {len(camps)} не хватает "
                       f"минус-фраз, которые уже используются в других "
                       f"кампаниях того же проекта. Эти кампании тянут трафик, "
                       f"который соседние уже отсекли.",
                object_type="account", object_id=ctx.account,
                evidence={"кампаний в проекте": len(camps),
                          "отсутствующие минус-фразы": gap},
                fix="Синхронизировать списки минус-фраз внутри проекта",
                fixable="manual",
            ))
    return out


# ---------------------------------------------------------- аномалии динамики

@register("DIRECT.ANOMALY", "direct", "warning",
          description="Аномалия в динамике показателей")
def check_deltas_anomalies(ctx):
    """Резкие скачки цены клика, расхода и падения конверсий к прошлому периоду."""
    if not ctx.loaded("deltas"):
        return []
    data = ctx.data["deltas"] or {}
    out = []
    for item in data.get("anomalies") or []:
        name = item.get("campaign_name")
        cid = item.get("campaign_id")
        money = None
        for row in data.get("campaigns") or []:
            if row.get("campaign_id") == cid:
                money = row.get("cost_now")
                break
        out.append(dict(
            title=f"Аномалия: {item.get('text')}",
            detail=f"Кампания «{name}»: {item.get('text')}. Период "
                   f"{data.get('period')} против {data.get('previous_period')}.",
            object_type="campaign", object_id=str(cid), object_name=name,
            evidence={"тип": item.get("kind"),
                      "период": data.get("period"),
                      "предыдущий период": data.get("previous_period")},
            money=money,
            fix="Разобраться в причине скачка: аукцион, ставки, "
                "конкуренты, сезонность",
            fixable="manual",
        ))
    return out

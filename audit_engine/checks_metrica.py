"""
Proverki Metrika: kachestvo dannyh i nalichie tselej.

Bez tselej avtostrategii Direkta ne mogut obuchat'sya — eto klyuchevaya
svyazka vsego audita.
"""

from .base import register

LOW_ACTIVITY = "low"
NO_DATA = "no_data"


def _counter_meta(counter):
    return {
        "object_type": "counter",
        "object_id": str(counter.get("id")),
        "object_name": counter.get("name"),
    }


@register("METRICA.NO_COUNTER", "metrica", "error",
          description="К аккаунту не привязан ни один счётчик Метрики")
def check_no_counter(ctx):
    if not ctx.loaded('counters'):
        return []
    counters = ctx.data.get("counters") or []
    if counters:
        return []
    return [dict(
        title="Нет счётчика Метрики",
        detail="Ни один счётчик не привязан к аккаунту: статистика и цели недоступны, "
               "автостратегии Директа нечему учиться",
        object_type="account", object_id=ctx.account,
        evidence={"привязано счётчиков": 0},
        fix="Выдать доступ к счётчику (ulogin) и привязать его в реестре",
        blocking=True,
    )]


@register("METRICA.NO_GOALS", "metrica", "error",
          description="На счётчике нет ни одной цели")
def check_no_goals(ctx):
    if not ctx.loaded('counters', 'goals'):
        return []
    out = []
    for counter in ctx.data.get("counters") or []:
        cid = counter.get("id")
        goals = (ctx.data.get("goals") or {}).get(cid)
        if goals is None:      # ne smogli poluchit' — uzhe v oshibkah
            continue
        if not goals:
            out.append(dict(
                title="На счётчике нет целей",
                detail="Без целей нельзя оценить конверсии, посчитать CPA и обучить "
                       "автостратегию Директа",
                evidence={"целей": 0},
                fix="Создать цели (форма, звонок, отправка заявки) в Метрике",
                blocking=True,
                **_counter_meta(counter),
            ))
    return out


@register("METRICA.NO_FAVORITE_GOALS", "metrica", "info",
          description="Ни одна цель не отмечена как ключевая")
def check_no_favorite_goals(ctx):
    if not ctx.loaded('counters'):
        return []
    out = []
    for counter in ctx.data.get("counters") or []:
        cid = counter.get("id")
        goals = (ctx.data.get("goals") or {}).get(cid) or []
        if goals and not any(g.get("is_favorite") for g in goals):
            out.append(dict(
                title="Не выбрана ключевая цель",
                detail=f"Из {len(goals)} целей ни одна не отмечена как ключевая — "
                       f"сложнее выбрать цель для автостратегий",
                evidence={"целей": len(goals)},
                fix="Отметить главную конверсионную цель как ключевую",
                **_counter_meta(counter),
            ))
    return out


@register("METRICA.NO_DATA", "metrica", "error",
          description="Счётчик не собирает данные")
def check_counter_no_data(ctx):
    if not ctx.loaded('counters'):
        return []
    out = []
    for counter in ctx.data.get("counters") or []:
        if str(counter.get("activity_status") or "").lower() == NO_DATA:
            out.append(dict(
                title="Счётчик не собирает данные",
                detail="По счётчику нет визитов: либо код не установлен, либо "
                       "на сайт не идёт трафик",
                evidence={"activity_status": counter.get("activity_status"),
                          "code_status": counter.get("code_status")},
                fix="Проверить установку кода счётчика на сайте",
                blocking=True,
                **_counter_meta(counter),
            ))
    return out


@register("METRICA.LOW_ACTIVITY", "metrica", "warning",
          description="Мало данных для решений")
def check_counter_low_activity(ctx):
    if not ctx.loaded('counters'):
        return []
    out = []
    for counter in ctx.data.get("counters") or []:
        status = str(counter.get("activity_status") or "").lower()
        if status == LOW_ACTIVITY:
            out.append(dict(
                title="Мало данных для решений",
                detail="Активность счётчика низкая: данных может не хватать для "
                       "надёжных выводов по конверсиям",
                evidence={"activity_status": counter.get("activity_status")},
                fix="Учитывать при оценке: выводы по конверсиям пока предварительные",
                **_counter_meta(counter),
            ))
    return out


@register("METRICA.CODE_STATUS", "metrica", "info",
          description="Состояние кода счётчика неизвестно")
def check_counter_code_status(ctx):
    if not ctx.loaded('counters'):
        return []
    out = []
    for counter in ctx.data.get("counters") or []:
        code_status = str(counter.get("code_status") or "")
        if code_status and not code_status.startswith("CS_OK"):
            out.append(dict(
                title="Состояние кода счётчика требует проверки",
                detail=f"code_status = {code_status}. Это может означать как ошибку "
                       f"установки кода, так и отсутствие проверки",
                evidence={"code_status": code_status},
                fix="Проверить код счётчика валидатором Метрики",
                **_counter_meta(counter),
            ))
    return out


@register("METRICA.GOALS_SCOPE", "metrica", "info",
          description="Какие цели счётчика работают в Директе, а какие — только аналитика")
def check_goals_scope(ctx):
    """
    Делим цели счётчика на две группы.

    В Директе участвуют только те цели, что выбраны в стратегиях кампаний:
    именно на них обучаются алгоритмы и за них мы платим. Остальные цели —
    воронка и аналитика Метрики: они показывают поведение людей на сайте,
    но в закупке не участвуют, и считать по ним цену заявки нельзя.
    """
    counters_goals = ctx.data.get("goals")
    if not counters_goals:
        return []

    # В глубоком режиме движок уже посчитал цели живых кампаний — берём их.
    direct_ids = sorted(int(g) for g in (ctx.data.get("direct_goal_ids") or []))
    if not direct_ids:
        resolved = ctx.data.get("priority_goals") or {}
        direct_ids = sorted({int(g) for gs in (resolved.get("api") or {}).values()
                             for g in gs})
        if not direct_ids:
            direct_ids = sorted({int(g) for gs in (resolved.get("goals") or {}).values()
                                 for g in gs})

    all_ids = []
    for goals in counters_goals.values():
        for goal in goals or []:
            gid = goal.get("id")
            if gid and int(gid) not in all_ids:
                all_ids.append(int(gid))
    if not all_ids:
        return []

    analytics_ids = [g for g in all_ids if g not in direct_ids]
    names = ctx.data.get("goal_names") or {}

    def label(goal_id):
        return names.get(goal_id) or f"цель {goal_id}"

    if not direct_ids:
        return [dict(
            severity="warning",
            title="В кампаниях Директа не выбрана ни одна цель Метрики",
            detail="На счётчике есть цели, но ни одна из них не включена в стратегии "
                   "кампаний: алгоритмы не получают сигнала о конверсиях и обучаться "
                   "не могут",
            evidence={"целей на счётчиках": len(all_ids),
                      "примеры": [label(g) for g in all_ids[:10]]},
            fix="Выбрать цель конверсии в стратегии каждой кампании",
            object_type="account", object_id=ctx.account,
        )]

    if not analytics_ids:
        return []

    return [dict(
        title="Цели счётчика: часть работает в Директе, часть — только аналитика",
        detail=f"В кампаниях Директа участвуют {len(direct_ids)} целей: "
               f"{', '.join(label(g) for g in direct_ids[:6])}. "
               f"Ещё {len(analytics_ids)} целей счётчика в кампании не включены — "
               f"это аналитика Метрики: она нужна для разбора поведения на сайте, "
               f"но на закупку и обучение алгоритмов не влияет, и цену заявки "
               f"по ней считать нельзя",
        evidence={"в Директе": [label(g) for g in direct_ids],
                  "только аналитика": [label(g) for g in analytics_ids[:20]],
                  "всего целей": len(all_ids)},
        fix="Ничего править не нужно — это справка. Меняем только те цели, "
            "что выбраны в стратегиях кампаний",
        object_type="account", object_id=ctx.account,
    )]
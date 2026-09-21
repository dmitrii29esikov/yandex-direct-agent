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

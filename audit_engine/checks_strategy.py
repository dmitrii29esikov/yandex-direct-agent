"""
Проверки стратегий: чем управляются ставки, чем ограничен расход и на какие
цели кампания оптимизируется.

Данные берутся из campaigns.get с дополнительными наборами полей
(TextCampaignFieldNames, UnifiedCampaignFieldNames, SmartCampaignFieldNames) —
см. access.campaign_strategies. До 22.09.2026 считалось, что инлайн-стратегия
через API не читается, поэтому весь этот пласт настроек в аудите отсутствовал.

Правило то же, что и в остальных проверках: одна проблема — одна находка,
а массовые вещи агрегируются по кампании.
"""

import access

from .base import register

MAX_EXAMPLES = 20

SCOPE_NAMES = {"Search": "поиск", "Network": "РСЯ и сети"}

STRATEGY_NAMES = {
    "SERVING_OFF": "показы выключены",
    "NETWORK_DEFAULT": "как в поиске",
    "PAY_FOR_CONVERSION": "оплата за конверсии",
    "PAY_FOR_CONVERSION_MULTIPLE_GOALS": "оплата за конверсии, несколько целей",
    "WB_MAXIMUM_CLICKS": "максимум кликов с ограничением ставки",
    "WB_MAXIMUM_CONVERSION_RATE": "максимум конверсий с ограничением ставки",
    "WB_MAXIMUM_CONVERSIONS": "максимум конверсий",
    "AVERAGE_CPC": "средняя цена клика (ручная ставка)",
    "AVERAGE_CPA": "средняя цена конверсии",
    "AVERAGE_CRR": "средняя доля рекламных расходов",
    "HIGHEST_POSITION": "наивысшая позиция (ручная ставка)",
}

# Модели атрибуции: AUTO — автоматическая, остальные — старые ручные модели.
LEGACY_ATTRIBUTION = {"LSCCD", "LSCD", "FC", "LC"}


def _loaded(ctx) -> bool:
    """Стратегии прочитаны? Если нет — ни одна проверка модуля не работает."""
    return ctx.data.get("strategies") is not None


def _strategy_name(code) -> str:
    return STRATEGY_NAMES.get(str(code), str(code) or "не определена")


def _campaigns(ctx, only_on: bool = False):
    if not ctx.loaded("campaigns"):
        return []
    items = []
    for c in ctx.data.get("campaigns") or []:
        state = c.get("State")
        if state == "ARCHIVED":
            continue
        if only_on and state != "ON":
            continue
        items.append(c)
    return items


def _strategy(ctx, campaign):
    """Разобранная стратегия кампании или None, если её не читали."""
    strategies = ctx.data.get("strategies") or {}
    try:
        return strategies.get(int(campaign.get("Id")))
    except (TypeError, ValueError):
        return None


def _meta(campaign):
    return {"object_type": "campaign", "object_id": str(campaign.get("Id")),
            "object_name": campaign.get("Name")}


def _channel_on(info, scope: str) -> bool:
    """Работают ли показы в канале. NETWORK_DEFAULT повторяет поиск."""
    own = (info.get("scopes") or {}).get(scope) or {}
    other = (info.get("scopes") or {}).get("Search" if scope == "Network" else "Network") or {}
    stype = own.get("type")
    if stype is None:
        return True                       # данных нет — не обвиняем
    if stype == access.OFF_TYPE:
        return False
    if stype in access.FOLLOW_TYPES:
        return other.get("type") not in (None, access.OFF_TYPE)
    return True


def _weekly_limit(info):
    """Недельный лимит кампании: берём максимальный из каналов."""
    limits = []
    for scope in access.SCOPES:
        value = ((info.get("scopes") or {}).get(scope) or {}).get("weekly_limit")
        if value:
            limits.append(float(value))
    return max(limits) if limits else None


# --------------------------------------------------------------------------
# Показы и цели
# --------------------------------------------------------------------------

@register("DIRECT.STRATEGY_ALL_CHANNELS_OFF", "direct", "error",
          description="Кампания включена, но показы выключены во всех каналах")
def check_all_channels_off(ctx):
    if not _loaded(ctx):
        return []
    out = []
    for c in _campaigns(ctx, only_on=True):
        info = _strategy(ctx, c)
        if not info:
            continue
        types = [(info.get("scopes") or {}).get(s) or {} for s in access.SCOPES]
        if all(t.get("type") is None for t in types):
            continue                      # стратегию не прочитали, не гадаем
        if not any(_channel_on(info, s) for s in access.SCOPES):
            out.append(dict(
                title="Кампания включена, но показы выключены во всех каналах",
                detail="И в поиске, и в сетях стоит «показы выключены»: "
                       "кампания числится запущенной, но реклама не показывается",
                evidence={"поиск": _strategy_name(types[0].get("type")),
                          "сети": _strategy_name(types[1].get("type")),
                          "состояние": c.get("State")},
                fix="Включить показы в нужном канале или остановить кампанию",
                blocking=True,
                **_meta(c),
            ))
    return out


@register("DIRECT.STRATEGY_GOAL_UNKNOWN", "direct", "error",
          description="Цель стратегии не найдена среди целей счётчиков кампании")
def check_strategy_goal_unknown(ctx):
    """
    Стратегия оптимизируется по цели, которой нет в Метрике кампании.

    Смотреть надо именно связку «цели стратегии — счётчики этой кампании»:
    в аккаунте несколько счётчиков, и чужая цель выглядит существующей,
    но обучение по ней не работает.
    """
    if not _loaded(ctx):
        return []
    goals_by_counter = ctx.data.get("goals")
    if goals_by_counter is None:
        return []                        # без целей Метрики судить не о чем

    all_known = set()
    for goals in goals_by_counter.values():
        for goal in goals or []:
            if goal.get("id") is not None:
                all_known.add(int(goal["id"]))
    if not all_known:
        return []

    out = []
    for c in _campaigns(ctx):
        info = _strategy(ctx, c)
        if not info:
            continue
        counters = info.get("counter_ids") or []
        own_goals = set()
        for counter_id in counters:
            for goal in goals_by_counter.get(counter_id) or []:
                if goal.get("id") is not None:
                    own_goals.add(int(goal["id"]))
        if not own_goals:
            continue                     # у счётчиков нет целей — не наша вина

        broken = [g for g in (info.get("goals_all") or []) if g not in own_goals]
        if not broken:
            continue
        nowhere = [g for g in broken if g not in all_known]
        out.append(dict(
            title="Стратегия оптимизируется на несуществующую цель",
            detail=f"Стратегия ссылается на цель {', '.join(str(g) for g in broken)}, "
                   f"которой нет среди целей счётчиков кампании "
                   f"({', '.join(str(c_ or '—') for c_ in counters)}). "
                   + ("Такой цели нет ни в одном счётчике аккаунта. " if nowhere else "")
                   + "Обучение по конверсиям работать не будет",
            evidence={"цели стратегии": info.get("goals_all"),
                      "цели своих счётчиков": sorted(own_goals),
                      "счётчики кампании": counters,
                      "состояние": c.get("State")},
            fix="Выбрать в стратегии кампании реальную цель Метрики "
                "(в интерфейсе Директа) или перевести канал на «оплату за конверсии»",
            blocking=True,
            **_meta(c),
        ))
    return out


# --------------------------------------------------------------------------
# Управление ставками
# --------------------------------------------------------------------------

@register("DIRECT.STRATEGY_MANUAL_BIDDING", "direct", "warning",
          description="Ручное управление ставками вместо автостратегии")
def check_manual_bidding(ctx):
    """
    HIGHEST_POSITION и AVERAGE_CPC — ручной режим: ставками управляет человек,
    цена клика не связана с конверсиями. В аккаунте с 2026 года норма —
    «оплата за конверсии, несколько целей».
    """
    if not _loaded(ctx):
        return []
    out = []
    for c in _campaigns(ctx):
        info = _strategy(ctx, c)
        if not info:
            continue
        for scope in access.SCOPES:
            s = (info.get("scopes") or {}).get(scope) or {}
            stype = s.get("type")
            if stype in access.MANUAL_TYPES:
                out.append(dict(
                    title="Ручное управление ставками",
                    detail=f"В канале «{SCOPE_NAMES[scope]}» стратегия «"
                           f"{_strategy_name(stype)}»: ставки выставляет человек, "
                           f"а не алгоритм по конверсиям. Такие кампании обычно "
                           f"переплачивают за клик и не масштабируются",
                    evidence={"канал": SCOPE_NAMES[scope],
                              "стратегия": _strategy_name(stype),
                              "недельный лимит ₽": s.get("weekly_limit"),
                              "состояние": c.get("State")},
                    fix="Перевести канал на «оплату за конверсии» или «максимум "
                        "конверсий», указав цель Метрики",
                    **(_meta(c) | {"object_id": f"{c.get('Id')}:{scope}"}),
                ))
            elif stype == "PAY_FOR_CONVERSION":
                out.append(dict(
                    severity="info",
                    title="Стратегия с одной целью",
                    detail=f"В канале «{SCOPE_NAMES[scope]}» оплата за конверсии "
                           f"по одной цели (CPA {s.get('cpa') or '—'} ₽). "
                           f"В аккаунте уже есть кампании с несколькими "
                           f"приоритетными целями — модель стоит унифицировать",
                    evidence={"канал": SCOPE_NAMES[scope],
                              "цель": (s.get("goals") or [None])[0],
                              "целевой CPA ₽": s.get("cpa"),
                              "состояние": c.get("State")},
                    fix="Перевести на «оплату за конверсии, несколько целей» "
                        "и задать приоритетные цели",
                    **(_meta(c) | {"object_id": f"{c.get('Id')}:{scope}"}),
                ))
    return out


@register("DIRECT.NO_WEEKLY_LIMIT", "direct", "warning",
          description="Автостратегия без недельного лимита расхода")
def check_no_weekly_limit(ctx):
    """Автостратегия без ограничения недельного расхода тратит без потолка."""
    if not _loaded(ctx):
        return []
    out = []
    for c in _campaigns(ctx, only_on=True):
        info = _strategy(ctx, c)
        if not info:
            continue
        for scope in access.SCOPES:
            s = (info.get("scopes") or {}).get(scope) or {}
            stype = s.get("type")
            if stype in access.AUTO_TYPES and not s.get("weekly_limit"):
                out.append(dict(
                    title="Не задан недельный лимит расхода",
                    detail=f"В канале «{SCOPE_NAMES[scope]}» работает автостратегия "
                           f"«{_strategy_name(stype)}», но лимит расхода не задан: "
                           f"кампания может израсходовать больше запланированного",
                    evidence={"канал": SCOPE_NAMES[scope],
                              "стратегия": _strategy_name(stype),
                              "состояние": c.get("State")},
                    fix="Задать недельный лимит расхода в стратегии кампании",
                    **(_meta(c) | {"object_id": f"{c.get('Id')}:{scope}"}),
                ))
    return out


@register("DIRECT.STRATEGY_NO_GOALS", "direct", "warning",
          description="В автостратегии не выбрана цель конверсии")
def check_strategy_no_goals(ctx):
    """
    Автостратегия есть, а цели нет: обучаться не на чем.

    Опасный случай в «оплате за конверсии» и в «максимуме конверсий»: без
    цели такие стратегии не могут считать конверсии и работают как обычный
    показ с непредсказуемой ценой результата.
    """
    if not _loaded(ctx):
        return []
    out = []
    for c in _campaigns(ctx, only_on=True):
        info = _strategy(ctx, c)
        if not info:
            continue
        active = [s for s in access.SCOPES
                  if ((info.get("scopes") or {}).get(s) or {}).get("type") in access.AUTO_TYPES]
        if not active:
            continue
        if info.get("goals_all"):
            continue
        out.append(dict(
            title="В автостратегии не выбрана цель конверсии",
            detail="Кампания управляется автостратегией "
                   f"({', '.join(_strategy_name(((info.get('scopes') or {}).get(s) or {}).get('type')) for s in active)}), "
                   "но ни приоритетных целей, ни цели в самой стратегии не задано: "
                   "конверсии не считаются, обучение невозможно",
            evidence={"каналы": active,
                      "счётчики кампании": info.get("counter_ids"),
                      "состояние": c.get("State")},
            fix="Выбрать цель Метрики в настройках стратегии кампании",
            blocking=True,
            **_meta(c),
        ))
    return out


@register("DIRECT.WEEKLY_LIMIT_VS_TARGET_CPA", "direct", "warning",
          description="Недельный лимит не покрывает целевые конверсии")
def check_weekly_limit_vs_target_cpa(ctx):
    """
    Сравниваем недельный лимит с целевой ценой заявки.

    Если лимит меньше одной целевой конверсии — кампания физически не может
    выполнить план. Если меньше трёх — стратегии не хватает данных для обучения,
    и она будет работать хуже, чем может.
    """
    if not _loaded(ctx):
        return []
    target = ctx.target_cpa
    if not target:
        return []

    out = []
    for c in _campaigns(ctx, only_on=True):
        info = _strategy(ctx, c)
        if not info:
            continue
        limit = _weekly_limit(info)
        if not limit:
            continue
        covers = limit / target
        if covers >= 3:
            continue
        severity = "warning" if covers < 1 else "info"
        if covers < 1:
            detail = (f"Недельный лимит {limit:.0f} ₽ меньше целевой цены заявки "
                      f"{target:.0f} ₽: даже одну целевую конверсию кампания "
                      f"отработать не может")
        else:
            detail = (f"Недельный лимит {limit:.0f} ₽ покрывает {covers:.1f} целевых "
                      f"заявок при цене {target:.0f} ₽. Для обучения автостратегии "
                      f"этого мало: нужно хотя бы 3 конверсии в неделю")
        out.append(dict(
            severity=severity,
            title="Недельный лимит не покрывает целевые конверсии",
            detail=detail,
            evidence={"недельный лимит ₽": limit, "целевой CPA": target,
                      "покрывает заявок в неделю": round(covers, 2),
                      "состояние": c.get("State")},
            fix="Поднять недельный лимит либо снизить целевую цену заявки "
                "осознанно (по факту, а не «чтобы влезло»)",
            money=limit,
            **_meta(c),
        ))
    return out


# --------------------------------------------------------------------------
# Сверка с реестром: цели и бюджет
# --------------------------------------------------------------------------

@register("DIRECT.GOALS_REGISTRY_DRIFT", "direct", "info",
          description="Приоритетные цели в реестре расходятся с API")
def check_goals_registry_drift(ctx):
    """
    Реестр был резервным источником, пока цели не читались из API.

    Теперь API — источник, а реестр — резерв, и любое расхождение означает,
    что в реестре устаревшие данные: их правили в интерфейсе или другим агентом.
    """
    resolved = ctx.data.get("priority_goals") or {}
    mismatch = resolved.get("mismatch") or {}
    if not mismatch:
        return []

    # Celi, kotoryh net v Metrike, iz sravneniya ubiraem: pro nih otdel'naya
    # proverka DIRECT.STRATEGY_GOAL_UNKNOWN, i pomekhat' reestr za nih nel'zya —
    # reestr kak raz molchit o takoy tseli.
    known = set()
    for goals in (ctx.data.get("goals") or {}).values():
        for goal in goals or []:
            if goal.get("id") is not None:
                known.add(int(goal["id"]))

    campaigns = {str(c.get("Id")): c for c in (ctx.data.get("campaigns") or [])}
    out = []
    for cid, sides in sorted(mismatch.items()):
        api_side = sides["api"]
        if known:
            api_side = [g for g in api_side if g in known]
        if api_side == sides["registry"]:
            continue
        campaign = campaigns.get(str(cid)) or {}
        out.append(dict(
            title="Приоритетные цели в реестре устарели",
            detail=f"API отдаёт цели {api_side or '—'}, а реестр — "
                   f"{sides['registry'] or '—'}. Реестр — резервный источник, "
                   f"и он разошёлся с аккаунтом",
            evidence={"из API": api_side, "из реестра": sides["registry"],
                      "исключено несуществующих целей": len(sides["api"]) - len(api_side)},
            fix="Привести direct.priority_goals в реестре к данным API "
                "(или удалить поле: цели теперь читаются сами)",
            object_type="campaign", object_id=str(cid),
            object_name=campaign.get("Name"),
        ))
    return out


@register("DIRECT.BUDGET_CHANGED", "direct", "warning",
          description="Недельный бюджет отличается от ожидаемого в реестре")
def check_budget_changed(ctx):
    """
    Отслеживание изменений бюджета между запусками.

    Бюджет двигают руками или другой агент — это нормально, но факт изменения
    должен быть виден. Ожидаемое значение задаётся в реестре:

        direct.expected_budget: {"710718813": 5500}   # по кампаниям
        direct.expected_budget: 12000                 # на весь аккаунт, ₽/нед

    Без этого поля проверка молчит: сравнивать не с чем.
    """
    if not _loaded(ctx):
        return []
    expected = (ctx.record.get("direct") or {}).get("expected_budget")
    if not expected:
        return []

    strategies = ctx.data.get("strategies") or {}
    current = {}
    for c in _campaigns(ctx, only_on=True):
        info = _strategy(ctx, c)
        if not info:
            continue
        limit = _weekly_limit(info)
        if limit:
            current[int(c.get("Id"))] = (limit, c)

    # Второй вариант записи: одно число на аккаунт.
    if isinstance(expected, (int, float)):
        total = sum(limit for limit, _ in current.values())
        if not total or abs(total - float(expected)) < 1:
            return []
        return [dict(
            title="Недельный бюджет аккаунта отличается от ожидаемого",
            detail=f"Ожидали {float(expected):.0f} ₽ в неделю, в аккаунте сейчас "
                   f"{total:.0f} ₽ ({total - float(expected):+.0f} ₽). "
                   f"Бюджет могли изменить в интерфейсе или другим агентом",
            evidence={"ожидаемо ₽": float(expected), "сейчас ₽": round(total, 2),
                      "кампаний учтено": len(current)},
            fix="Подтвердить, что бюджет изменён осознанно, и обновить "
                "direct.expected_budget в реестре",
            object_type="account", object_id=ctx.account,
        )]

    out = []
    for cid, want in expected.items():
        try:
            cid, want = int(cid), float(want)
        except (TypeError, ValueError):
            continue
        found = current.get(cid)
        if not found:
            continue
        limit, campaign = found
        if abs(limit - want) < 1:
            continue
        delta = limit - want
        out.append(dict(
            title="Недельный бюджет кампании изменился",
            detail=f"Ожидали {want:.0f} ₽ в неделю, сейчас {limit:.0f} ₽ "
                   f"({delta:+.0f} ₽, {delta / want * 100:+.0f}%). Бюджет двигали "
                   f"в интерфейсе или другим агентом — это не поломка, но факт "
                   f"изменения фиксируем",
            evidence={"ожидаемо ₽": want, "сейчас ₽": limit,
                      "разница ₽": round(delta, 2)},
            fix="Подтвердить изменение и обновить direct.expected_budget "
                "в реестре либо вернуть прежний лимит в стратегии",
            **_meta(campaign),
        ))
    return out


@register("DIRECT.ATTRIBUTION_MODEL_LEGACY", "direct", "info",
          description="Устаревшая модель атрибуции в кампании")
def check_attribution_model_legacy(ctx):
    """LSCCD и родственные модели — ручные; в аккаунте норма — AUTO."""
    if not _loaded(ctx):
        return []
    out = []
    for c in _campaigns(ctx, only_on=True):
        info = _strategy(ctx, c)
        if not info:
            continue
        model = info.get("attribution_model")
        if model and str(model).upper() in LEGACY_ATTRIBUTION:
            out.append(dict(
                title="Устаревшая модель атрибуции",
                detail=f"В кампании выбрана модель атрибуции «{model}» "
                       f"(по последнему клику). Автоматическая модель (AUTO) "
                       f"распределяет ценность конверсии по всем касаниям "
                       f"и точнее показывает вклад каналов",
                evidence={"модель атрибуции": model},
                fix="Переключить модель атрибуции на автоматическую",
                **_meta(c),
            ))
    return out

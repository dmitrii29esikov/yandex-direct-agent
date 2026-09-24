"""
Proverki Direct: struktura, teksty, stavki i byudzhet, moderatsiya.

Pravilo: odna problema na ob'ekt, no tam, gde problemy massovye (tekst dlinnee
normy v sotnyah obyavlenij), agregiruem po kampanii — inache otchet stanet
svalkoj iz tysyach strok, a ne spiskom zadach.
"""

from collections import Counter

import access

from .base import register

# Limity iz spravki Direkta dlya tekstovo-graficheskih obyavlenij.
TITLE_MAX = 56
TEXT_MAX = 81
MAX_EXAMPLES = 20


def _group_by(items, key):
    out = {}
    for item in items or []:
        out.setdefault(str(item.get(key)), []).append(item)
    return out


def _stats_for(ctx, campaign_id):
    return (ctx.data.get("stats") or {}).get(str(campaign_id), {})


def _campaign_meta(campaign):
    return {"object_type": "campaign", "object_id": str(campaign.get("Id")),
            "object_name": campaign.get("Name")}


# --------------------------------------------------------------------------
# Struktura
# --------------------------------------------------------------------------

@register("DIRECT.NO_ACTIVE_CAMPAIGNS", "direct", "error",
          description="Ни одна кампания не показывается")
def check_no_active(ctx):
    if not ctx.loaded('campaigns'):
        return []
    campaigns = ctx.data.get("campaigns") or []
    if not campaigns or any(c.get("State") == "ON" for c in campaigns):
        return []

    by_state = Counter(str(c.get("State")) for c in campaigns)
    return [dict(
        title="Нет ни одной запущенной кампании",
        detail="Все кампании остановлены или в архиве: " +
               ", ".join(f"{k} — {v}" for k, v in by_state.items()),
        object_type="account", object_id=ctx.account, object_name=ctx.account,
        evidence={"всего кампаний": len(campaigns), "по состояниям": dict(by_state)},
        fix="Запустить нужные кампании или вывести их из архива",
        blocking=True,
    )]


@register("DIRECT.EMPTY_CAMPAIGN", "direct", "error",
          description="Кампания без групп или без объявлений")
def check_empty_campaign(ctx):
    if not ctx.loaded('adgroups', 'ads'):
        return []
    groups = _group_by(ctx.data.get("adgroups"), "CampaignId")
    ads = _group_by(ctx.data.get("ads"), "CampaignId")
    out = []
    for c in ctx.data.get("campaigns") or []:
        cid = str(c.get("Id"))
        n_groups = len(groups.get(cid, []))
        n_ads = len(ads.get(cid, []))
        if n_groups == 0 or n_ads == 0:
            out.append(dict(
                title="Кампания не наполнена",
                detail=f"Групп: {n_groups}, объявлений: {n_ads}",
                evidence={"групп": n_groups, "объявлений": n_ads},
                fix="Добавить группы и объявления либо остановить кампанию",
                **_campaign_meta(c),
            ))
    return out


@register("DIRECT.ADGROUP_NO_ADS", "direct", "warning",
          description="Группа без объявлений")
def check_adgroup_without_ads(ctx):
    if not ctx.loaded('ads'):
        return []
    ads = _group_by(ctx.data.get("ads"), "AdGroupId")
    out = []
    for g in ctx.data.get("adgroups") or []:
        if not ads.get(str(g.get("Id"))):
            out.append(dict(
                title="Группа без объявлений",
                detail="В группе нет ни одного объявления — показы невозможны",
                object_type="adgroup", object_id=str(g.get("Id")),
                object_name=g.get("Name"),
                evidence={"кампания": g.get("CampaignId")},
                fix="Добавить объявление в группу",
            ))
    return out


@register("DIRECT.ADGROUP_NO_KEYWORDS", "direct", "warning",
          description="Группа без ключевых фраз")
def check_adgroup_without_keywords(ctx):
    if not ctx.loaded('keywords'):
        return []
    kws = _group_by(ctx.data.get("keywords"), "AdGroupId")
    out = []
    for g in ctx.data.get("adgroups") or []:
        if not kws.get(str(g.get("Id"))):
            out.append(dict(
                title="Группа без ключевых фраз",
                detail="Нет фраз и не включён автотаргетинг — трафика не будет",
                object_type="adgroup", object_id=str(g.get("Id")),
                object_name=g.get("Name"),
                evidence={"кампания": g.get("CampaignId")},
                fix="Добавить фразы или включить автотаргетинг",
            ))
    return out


@register("DIRECT.DUPLICATE_KEYWORDS", "direct", "warning",
          description="Одна фраза в нескольких группах кампании")
def check_duplicate_keywords(ctx):
    if not ctx.loaded('keywords'):
        return []
    kws = _group_by(ctx.data.get("keywords"), "CampaignId")
    campaigns = {str(c.get("Id")): c for c in (ctx.data.get("campaigns") or [])}
    out = []
    for cid, items in kws.items():
        seen = {}
        for k in items:
            text = " ".join(str(k.get("Keyword") or "").lower().split())
            if text:
                seen.setdefault(text, set()).add(str(k.get("AdGroupId")))
        dupes = {t: sorted(g) for t, g in seen.items() if len(g) > 1}
        if dupes:
            out.append(dict(
                title="Дубли фраз в разных группах",
                detail=f"{len(dupes)} фраз встречаются более чем в одной группе — "
                       f"группы конкурируют друг с другом на аукционе",
                evidence={"примеры": list(dupes.items())[:MAX_EXAMPLES],
                          "всего": len(dupes)},
                fix="Оставить фразу в одной, самой релевантной группе",
                **(campaigns.get(cid) and _campaign_meta(campaigns[cid])
                   or {"object_type": "campaign", "object_id": cid}),
            ))
    return out


@register("DIRECT.GROUP_NO_NEGATIVES", "direct", "info",
          description="Группа без минус-фраз")
def check_group_without_negatives(ctx):
    if not ctx.loaded('adgroups'):
        return []
    out = []
    for g in ctx.data.get("adgroups") or []:
        if not (g.get("NegativeKeywords") or {}).get("Items"):
            out.append(dict(
                title="Группа без минус-фраз",
                detail="Минус-фразы не заданы ни на группе, ни (проверьте) на кампании",
                object_type="adgroup", object_id=str(g.get("Id")),
                object_name=g.get("Name"),
                evidence={"кампания": g.get("CampaignId")},
                fix="Добавить минус-фразы уровня группы",
            ))
    return out


# --------------------------------------------------------------------------
# Teksty obyavlenij
# --------------------------------------------------------------------------

@register("DIRECT.TEXT_LENGTH", "direct", "info", fixable="auto",
          technical=True,
          description="Превышена длина заголовка или текста объявления")
def check_text_length(ctx):
    if not ctx.loaded('ads'):
        return []
    campaigns = {str(c.get("Id")): c for c in (ctx.data.get("campaigns") or [])}
    problems = {}
    skipped_accepted = 0
    for ad in ctx.data.get("ads") or []:
        text_ad = ad.get("TextAd") or {}
        if not text_ad:
            continue
        # Moderatsiya — istochnik pravdy. Esli obyavlenie prinyato, znachit
        # dlina v poryadke: u ЕПК tekst mozhet byt dlinnee 81, i my ne dolzhny
        # tashchit' eto v otchet. Arhivnye tozhe propuskaem.
        if str(ad.get("Status")) == "ACCEPTED":
            skipped_accepted += 1
            continue
        if str(ad.get("State")) == "ARCHIVED":
            continue
        cid = str(ad.get("CampaignId"))
        issues = []
        for field, limit in (("Title", TITLE_MAX), ("Title2", TITLE_MAX), ("Text", TEXT_MAX)):
            value = text_ad.get(field)
            if value and len(str(value)) > limit:
                issues.append({"поле": field, "длина": len(str(value)), "лимит": limit})
        if issues:
            problems.setdefault(cid, []).append(
                {"ad_id": str(ad.get("Id")), "проблемы": issues})

    out = []
    for cid, items in problems.items():
        campaign = campaigns.get(cid) or {}
        out.append(dict(
            title="Превышение длины текста объявлений",
            detail=f"Объявлений с превышением лимита: {len(items)} "
                   f"(заголовок {TITLE_MAX}, текст {TEXT_MAX} символов) — "
                   f"Директ отклоняет такие объявления",
            evidence={"объявлений с превышением": len(items),
                      "пропущено принятых модерацией": skipped_accepted,
                      "примеры": items[:MAX_EXAMPLES]},
            fix="Сократить заголовок до 56 и текст до 81 символа",
            **_campaign_meta(campaign) if campaign
            else {"object_type": "campaign", "object_id": cid},
        ))
    return out


@register("DIRECT.NO_SITELINKS", "direct", "warning",
          description="У объявлений нет быстрых ссылок")
def check_no_sitelinks(ctx):
    if not ctx.loaded('ads'):
        return []
    campaigns = {str(c.get("Id")): c for c in (ctx.data.get("campaigns") or [])}
    without = {}
    total = {}
    for ad in ctx.data.get("ads") or []:
        text_ad = ad.get("TextAd") or {}
        if not text_ad:
            continue
        cid = str(ad.get("CampaignId"))
        total[cid] = total.get(cid, 0) + 1
        if not text_ad.get("SitelinkSetId"):
            without[cid] = without.get(cid, 0) + 1

    out = []
    for cid, n in without.items():
        if n and n == total.get(cid):
            campaign = campaigns.get(cid) or {}
            out.append(dict(
                title="Нет быстрых ссылок",
                detail=f"У всех {n} объявлений кампании не заданы быстрые ссылки — "
                       f"они увеличивают размер объявления и CTR",
                evidence={"объявлений без быстрых ссылок": n},
                fix="Добавить набор быстрых ссылок (минимум 4)",
                **_campaign_meta(campaign) if campaign
                else {"object_type": "campaign", "object_id": cid},
            ))
    return out


@register("DIRECT.NO_CALLOUTS", "direct", "info",
          description="У объявлений нет уточнений")
def _has_callouts(ext) -> bool:
    """
    Utochneniya (callouts) v API prihodyat kak spisok rasshirenij
    [{'AdExtensionId': ..., 'Type': 'CALLOUT'}, ...]. No byvayut i drugie formy,
    poetomu razbiraem akkuratno.
    """
    if isinstance(ext, dict):
        return bool(ext.get("Callouts")) or str(ext.get("Type", "")).upper() == "CALLOUT"
    if isinstance(ext, list):
        return any(
            isinstance(item, dict) and str(item.get("Type", "")).upper() == "CALLOUT"
            for item in ext
        )
    return False


def check_no_callouts(ctx):
    if not ctx.loaded('ads'):
        return []
    campaigns = {str(c.get("Id")): c for c in (ctx.data.get("campaigns") or [])}
    without = {}
    total = {}
    for ad in ctx.data.get("ads") or []:
        text_ad = ad.get("TextAd") or {}
        if not text_ad or "AdExtensions" not in text_ad:
            continue          # pole nedostupno — chestno propuskaem proverku
        cid = str(ad.get("CampaignId"))
        total[cid] = total.get(cid, 0) + 1
        ext = text_ad.get("AdExtensions")
        if not _has_callouts(ext):
            without[cid] = without.get(cid, 0) + 1

    out = []
    for cid, n in without.items():
        if n and n == total.get(cid):
            campaign = campaigns.get(cid) or {}
            out.append(dict(
                title="Нет уточнений",
                detail=f"Ни у одного из {n} объявлений нет уточнений",
                evidence={"объявлений без уточнений": n},
                fix="Добавить уточнения (до 25 штук, по 25 символов)",
                **_campaign_meta(campaign) if campaign
                else {"object_type": "campaign", "object_id": cid},
            ))
    return out


# --------------------------------------------------------------------------
# Byudzhet, stavki, moderatsiya
# --------------------------------------------------------------------------

@register("DIRECT.NO_DAILY_BUDGET", "direct", "warning",
          description="Нет дневного бюджета при ручных ставках и без недельного лимита")
def check_no_daily_budget(ctx):
    """Флаг только там, где риск реальный: ручные ставки и отсутствие недельного
    лимита. Автостратегия с недельным бюджетом сама распределяет расход по
    неделе, для неё проверка бессмысленна. Без данных о стратегиях проверка
    молчит — не выдаём флаг вслепую."""
    if not ctx.loaded('campaigns'):
        return []
    strategies = ctx.data.get("strategies") or {}
    if not strategies:
        return []
    out = []
    for c in ctx.data.get("campaigns") or []:
        if c.get("State") == "ARCHIVED":
            continue
        if c.get("DailyBudget"):
            continue
        info = _strategy_info(strategies, c.get("Id"))
        if _weekly_limit(info) or not _is_manual_strategy(info):
            continue
        out.append(dict(
            title="Не задан дневной бюджет",
            detail="Ручные ставки и нет недельного лимита: расход ничем не ограничен",
            evidence={"состояние": c.get("State"), "статус": c.get("Status"),
                      "стратегия ручная": True, "недельный лимит": 0},
            fix="Задать дневной бюджет или недельный лимит в стратегии",
            **_campaign_meta(c),
        ))
    return out


# Ручные стратегии: у них нет внутреннего распределения бюджета по неделе
MANUAL_STRATEGY_TYPES = {
    "AVERAGE_CPC", "AVERAGE_CPA", "MANUAL_CPC", "MANUAL_CPA",
    "HIGHEST_POSITION", "HIGHEST_POSITION_MULTIPLE_GOALS",
}


def _strategy_info(strategies, campaign_id):
    """Стратегии кампании в любой из форм ключа (int/str)."""
    if not isinstance(strategies, dict):
        return {}
    info = strategies.get(campaign_id)
    if info is None:
        info = strategies.get(str(campaign_id))
    return info if isinstance(info, dict) else {}


def _weekly_limit(info):
    """Недельный лимит кампании в микроединицах (0 = лимита нет)."""
    values = []
    top = info.get("weekly_limit") or info.get("weekly_limit_micros")
    if top:
        values.append(int(top))
    for scope in (info.get("scopes") or {}).values():
        if isinstance(scope, dict):
            limit = scope.get("weekly_limit") or scope.get("weekly_limit_micros")
            if limit:
                values.append(int(limit))
    return max(values) if values else 0


def _is_manual_strategy(info):
    """Только ручное управление ставками."""
    for scope in (info.get("scopes") or {}).values():
        if isinstance(scope, dict) and \
                str(scope.get("type") or "") in MANUAL_STRATEGY_TYPES:
            return True
    return False


def _economy(ctx, campaign) -> dict:
    """
    Экономика кампании: сколько стоит ЦЕЛЕВАЯ заявка.

    Считаем по приоритетным целям кампании, а не по всем целям счётчика:
    микродействия («переход в магазин», «вставка ссылки») иначе маскируют
    отсутствие заявок. Если приоритетные цели прочитать не удалось — честно
    помечаем источник в доказательствах.
    """
    base = _stats_for(ctx, campaign.get("Id")) or {}
    result = {
        "расход": base.get("cost"),
        "клики": base.get("clicks"),
        "все_конверсии": base.get("conversions"),
        "цена_по_всем_целям": base.get("cpa"),
        "конверсии": base.get("conversions"),
        "цена": base.get("cpa"),
        "цели": None,
        "источник": "все цели счётчика" if base else None,
    }
    targeted = (ctx.data.get("stats_targeted") or {}).get(str(campaign.get("Id")))
    if targeted:
        result.update({
            "конверсии": targeted.get("conversions") or 0,
            "цена": targeted.get("cpa"),
            "цели": targeted.get("goals"),
            "источник": "приоритетные цели кампании",
        })
    return result


@register("DIRECT.SPEND_NO_CONVERSION", "direct", "error",
          description="Есть расход, но нет целевых заявок")
def check_spend_without_conversion(ctx):
    """
    Расход есть, а целевых заявок нет.

    Считаем именно целевые конверсии (приоритетные цели кампании): иначе
    микродействия вроде «переход в магазин» маскируют отсутствие заявок.
    Обе цифры попадают в доказательства, чтобы не было вопросов, откуда число.
    """
    if not ctx.loaded('campaigns', 'stats'):
        return []
    out = []
    for c in ctx.data.get("campaigns") or []:
        eco = _economy(ctx, c)
        cost = eco.get("расход") or 0
        conversions = eco.get("конверсии") or 0
        if cost <= 0 or conversions:
            continue
        all_conv = eco.get("все_конверсии") or 0
        detail = f"Израсходовано {cost:.0f} ₽, целевых заявок — ноль."
        if all_conv:
            detail += (f" При этом по всем целям счётчика набралось {all_conv:.0f} — "
                       f"это микродействия, а не обращения.")
        detail += " Либо трафик нецелевой, либо цель выбрана не та."
        out.append(dict(
            title="Расход без целевых заявок",
            detail=detail,
            evidence={"расход": cost, "целевые конверсии": conversions,
                      "все конверсии счётчика": all_conv,
                      "источник конверсий": eco.get("источник"),
                      "клики": eco.get("клики")},
            fix="Разобрать площадки и запросы, затем сменить стратегию на оплату "
                "за конверсии с приоритетными целями",
            blocking=True,
            money=cost,
            **_campaign_meta(c),
        ))
    return out


@register("DIRECT.CPA_ABOVE_TARGET", "direct", "warning",
          description="Цена целевой заявки выше целевой")
def check_cpa_above_target(ctx):
    """
    Сравниваем цену ЦЕЛЕВОЙ заявки с целевой ценой из реестра.

    Целевые заявки — по приоритетным целям кампании; если их прочитать
    не удалось, в доказательствах видно, что считали по всем целям счётчика.
    """
    if not ctx.loaded('campaigns', 'stats'):
        return []
    target = ctx.target_cpa
    if not target:
        return []
    out = []
    for c in ctx.data.get("campaigns") or []:
        eco = _economy(ctx, c)
        cpa = eco.get("цена")
        if not cpa or cpa <= target:
            continue
        out.append(dict(
            title="Дорогая заявка",
            detail=f"Цена целевой заявки {cpa:.0f} ₽ при целевой {target:.0f} ₽ "
                   f"({cpa / target:.1f}× от цели): расход "
                   f"{eco.get('расход') or 0:.0f} ₽ на "
                   f"{eco.get('конверсии') or 0:.0f} заявок",
            evidence={"цена заявки": round(cpa, 2), "целевой CPA": target,
                      "расход": eco.get("расход"),
                      "конверсии": eco.get("конверсии"),
                      "источник конверсий": eco.get("источник"),
                      "прочие конверсии счётчика": eco.get("все_конверсии")},
            fix="Снизить ставки, исключить неэффективные площадки и запросы, "
                "перейти на автостратегию с целевой ценой конверсии",
            money=eco.get("расход"),
            **_campaign_meta(c),
        ))
    return out


@register("DIRECT.REJECTED_ADS", "direct", "error",
          description="Объявления, отклонённые модерацией")
def check_rejected_ads(ctx):
    if not ctx.loaded('ads'):
        return []
    campaigns = {str(c.get("Id")): c for c in (ctx.data.get("campaigns") or [])}
    rejected = {}
    for ad in ctx.data.get("ads") or []:
        if str(ad.get("Status")) in ("REJECTED", "PRE_REJECTED"):
            cid = str(ad.get("CampaignId"))
            rejected.setdefault(cid, []).append({
                "ad_id": str(ad.get("Id")),
                "статус": ad.get("Status"),
                "причина": ad.get("StatusClarification"),
            })

    out = []
    for cid, items in rejected.items():
        campaign = campaigns.get(cid) or {}
        out.append(dict(
            title="Объявления отклонены модерацией",
            detail=f"{len(items)} объявлений не прошли модерацию и не показываются",
            evidence={"примеры": items[:MAX_EXAMPLES], "всего": len(items)},
            fix="Исправить причину отклонения и отправить на повторную модерацию",
            **_campaign_meta(campaign) if campaign
            else {"object_type": "campaign", "object_id": cid},
        ))
    return out


@register("DIRECT.STATUS_PAYMENT", "direct", "error",
          description="Проблемы с оплатой аккаунта")
def check_status_payment(ctx):
    if not ctx.loaded('campaigns'):
        return []
    bad = [c for c in (ctx.data.get("campaigns") or [])
           if c.get("StatusPayment") not in (None, "ALLOWED")]
    if not bad:
        return []
    return [dict(
        title="Оплата не разрешена",
        detail=f"{len(bad)} кампаний имеют статус оплаты, отличный от ALLOWED",
        evidence={"кампании": [{"id": c.get("Id"), "status_payment": c.get("StatusPayment")}
                               for c in bad[:MAX_EXAMPLES]]},
        fix="Пополнить баланс или разобраться с задолженностью",
        blocking=True,
        object_type="account", object_id=ctx.account,
    )]


@register("DIRECT.CLICKS_WITHOUT_COST", "direct", "info",
          description="Есть клики, но расход не начисляется")
def check_clicks_without_cost(ctx):
    """
    Клики есть, а расход нулевой за период.

    Раньше мы не могли различить три разных случая, потому что стратегия не
    читалась:
      - «оплата за конверсии»: клики бесплатны, платим только за конверсию —
        ноль расхода при нуле конверсий это норма, а не потеря;
      - любая другая стратегия: расход начисляется за клики, значит дело либо
        в задержке атрибуции расходов, либо в состоянии оплаты;
      - стратегию прочитать не удалось — формулируем осторожно.
    """
    if not ctx.loaded("campaigns", "stats"):
        return []

    strategies = ctx.data.get("strategies")
    out = []
    for campaign in ctx.data.get("campaigns") or []:
        stats = (ctx.data.get("stats") or {}).get(str(campaign.get("Id"))) or {}
        clicks = stats.get("clicks") or 0
        cost = stats.get("cost") or 0
        if not (clicks > 0 and cost == 0):
            continue

        info = None
        if strategies is not None:
            try:
                info = (strategies or {}).get(int(campaign.get("Id")))
            except (TypeError, ValueError):
                info = None

        pays_for_conversion, others = [], []
        for scope, scope_info in ((info or {}).get("scopes") or {}).items():
            stype = str(scope_info.get("type") or "")
            if not stype or stype == access.OFF_TYPE:
                continue
            if stype.startswith("PAY_FOR_CONVERSION"):
                pays_for_conversion.append(stype)
            else:
                others.append(stype)

        if info and pays_for_conversion and not others:
            out.append(dict(
                severity="info",
                title="Клики без расхода — так работает оплата за конверсии",
                detail=f"{int(clicks)} кликов и 0 ₽ расхода. Стратегия кампании — "
                       f"«оплата за конверсии» ({', '.join(pays_for_conversion)}): "
                       f"клики бесплатны, платим только за конверсию. При нуле "
                       f"конверсий ноль расхода — ожидаемое поведение модели, "
                       f"а не потеря денег",
                evidence={"клики": clicks, "расход": cost,
                          "конверсии": stats.get("conversions"),
                          "стратегии": pays_for_conversion},
                fix="Вмешательство не требуется. Если конверсий нет неделями — "
                    "разбирайте цели и поисковые запросы",
                **_campaign_meta(campaign),
            ))
        elif info and others:
            out.append(dict(
                severity="warning",
                title="Клики есть, расход не начисляется",
                detail=f"{int(clicks)} кликов и 0 ₽ расхода, при этом стратегия — "
                       f"«{others[0]}»: в этой модели расход начисляется за клики. "
                       f"Проверьте состояние оплаты и задержку отчётности",
                evidence={"клики": clicks, "расход": cost,
                          "конверсии": stats.get("conversions"),
                          "стратегии": others},
                fix="Проверить оплату аккаунта и отчёт по расходам за сегодня",
                **_campaign_meta(campaign),
            ))
        else:
            out.append(dict(
                severity="info",
                title="Клики без расхода",
                detail=f"{int(clicks)} кликов и 0 ₽ расхода за период. Чаще всего "
                       f"это модель оплаты за конверсии (клики бесплатны, платим "
                       f"за конверсию), реже — задержка атрибуции расходов. "
                       f"Стратегию прочитать не удалось",
                evidence={"клики": clicks, "расход": cost,
                          "конверсии": stats.get("conversions")},
                fix="Убедиться, что модель оплаты и цель выбраны осознанно",
                **_campaign_meta(campaign),
            ))
    return out

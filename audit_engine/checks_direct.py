"""
Proverki Direct: struktura, teksty, stavki i byudzhet, moderatsiya.

Pravilo: odna problema na ob'ekt, no tam, gde problemy massovye (tekst dlinnee
normy v sotnyah obyavlenij), agregiruem po kampanii — inache otchet stanet
svalkoj iz tysyach strok, a ne spiskom zadach.
"""

from collections import Counter

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

@register("DIRECT.TEXT_LENGTH", "direct", "error", fixable="auto",
          description="Превышена длина заголовка или текста объявления")
def check_text_length(ctx):
    campaigns = {str(c.get("Id")): c for c in (ctx.data.get("campaigns") or [])}
    problems = {}
    for ad in ctx.data.get("ads") or []:
        text_ad = ad.get("TextAd") or {}
        if not text_ad:
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
                      "примеры": items[:MAX_EXAMPLES]},
            fix="Сократить заголовок до 56 и текст до 81 символа",
            **_campaign_meta(campaign) if campaign
            else {"object_type": "campaign", "object_id": cid},
        ))
    return out


@register("DIRECT.NO_SITELINKS", "direct", "warning",
          description="У объявлений нет быстрых ссылок")
def check_no_sitelinks(ctx):
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
          description="Нет дневного бюджета при ручном управлении")
def check_no_daily_budget(ctx):
    out = []
    for c in ctx.data.get("campaigns") or []:
        if c.get("State") == "ARCHIVED":
            continue
        if not c.get("DailyBudget"):
            out.append(dict(
                title="Не задан дневной бюджет",
                detail="Кампания может израсходовать весь недельный бюджет за день, "
                       "после чего показы прекратятся",
                evidence={"состояние": c.get("State"), "статус": c.get("Status")},
                fix="Задать дневной бюджет и режим его распределения",
                **_campaign_meta(c),
            ))
    return out


@register("DIRECT.SPEND_NO_CONVERSION", "direct", "error",
          description="Есть расход, но нет конверсий")
def check_spend_without_conversion(ctx):
    out = []
    for c in ctx.data.get("campaigns") or []:
        stats = _stats_for(ctx, c.get("Id"))
        cost = stats.get("cost") or 0
        conversions = stats.get("conversions") or 0
        if cost > 0 and conversions == 0:
            out.append(dict(
                title="Расход без конверсий",
                detail=f"Израсходовано {cost:.0f} ₽ и ни одной конверсии. "
                       f"Либо цели не настроены, либо трафик нецелевой",
                evidence={"расход": cost, "конверсии": conversions,
                          "клики": stats.get("clicks")},
                fix="Проверить связку с целями Метрики и передачу цели в Директ, "
                    "затем разобрать поисковые запросы",
                blocking=True,
                money=cost,
                **_campaign_meta(c),
            ))
    return out


@register("DIRECT.CPA_ABOVE_TARGET", "direct", "warning",
          description="Фактическая цена конверсии выше целевой")
def check_cpa_above_target(ctx):
    target = ctx.data.get("target_cpa")
    if not target:
        return []
    out = []
    for c in ctx.data.get("campaigns") or []:
        stats = _stats_for(ctx, c.get("Id"))
        cpa = stats.get("cpa")
        if cpa and cpa > target:
            out.append(dict(
                title="Дорогая конверсия",
                detail=f"Фактическая цена конверсии {cpa:.0f} ₽ при целевой {target:.0f} ₽ "
                       f"({cpa / target:.1f}× от цели)",
                evidence={"CPA": cpa, "целевой CPA": target,
                          "расход": stats.get("cost"),
                          "конверсии": stats.get("conversions")},
                fix="Снизить ставки или ограничить неэффективные площадки/запросы",
                money=stats.get("cost"),
                **_campaign_meta(c),
            ))
    return out


@register("DIRECT.REJECTED_ADS", "direct", "error",
          description="Объявления, отклонённые модерацией")
def check_rejected_ads(ctx):
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

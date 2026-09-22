"""
Экономика заявок: сколько они стоят и сколько должны стоить.

Владельцу важнее не «сколько находок», а ответ на вопрос «не переплачиваем
ли мы за заявки». Здесь три ориентира:

  - план: goals.target_cpa в реестре;
  - рынок: direct.market_cpa в реестре (сколько заявка стоит в нише);
  - внутренний эталон: лучшая по цене заявки кампания этого же аккаунта.

Целевые заявки берём по приоритетным целям кампаний
(см. fetchers.fetch_stats_targeted), а не по всем целям счётчика.
"""

import access

from .base import register
from .checks_direct import _economy


def _meta(campaign):
    return {"object_type": "campaign", "object_id": str(campaign.get("Id")),
            "object_name": campaign.get("Name")}


def _market_cpa(ctx, campaign=None):
    """
    Рыночная цена заявки из реестра: direct.market_cpa или goals.market_cpa.

    Значение может быть числом (одно на аккаунт) либо словарём по кампаниям:
    в одном аккаунте живут разные проекты, и у каждого своя рыночная цена.
        "direct": {"market_cpa": {"710718813": 3500, "710694370": 1500}}
    """
    record = ctx.record or {}
    market = ((record.get("direct") or {}).get("market_cpa")
              or (record.get("goals") or {}).get("market_cpa"))
    if isinstance(market, dict):
        if not campaign:
            return None
        cid = str(campaign.get("Id"))
        value = market.get(cid)
        if value is None:
            value = market.get(int(cid)) if cid.isdigit() else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    return market


@register("DIRECT.LEAD_COST_BENCHMARK", "direct", "info",
          description="Заявка дороже, чем в лучшей кампании этого же аккаунта")
def check_lead_cost_benchmark(ctx):
    """
    Сравниваем цену заявки каждой кампании с лучшей в аккаунте.

    Это самый честный рыночный ориентир, который у нас всегда есть: если одна
    кампания приносит заявку за 300 ₽, а другая за 3 000 ₽ — вопрос не в рынке,
    а в настройках. Если в реестре задан market_cpa, добавляем и его.
    """
    if not ctx.loaded('campaigns', 'stats'):
        return []

    rows = []
    for c in ctx.data.get("campaigns") or []:
        eco = _economy(ctx, c)
        if (eco.get("расход") or 0) <= 0 or not eco.get("цена"):
            continue
        rows.append((c, eco))
    if len(rows) < 2:
        return []

    best_campaign, best = min(rows, key=lambda item: item[1]["цена"])
    best_price = best["цена"]
    out = []
    for c, eco in rows:
        market = _market_cpa(ctx, c)
        price = eco["цена"]
        if price <= best_price * 1.5:
            continue                      # в пределах полутора раз — не шумим
        parts = [f"Цена заявки {price:.0f} ₽ — в {price / best_price:.1f} раза дороже "
                 f"лучшей кампании аккаунта ({best_price:.0f} ₽, "
                 f"«{(best_campaign.get('Name') or '')[:40]}»)"]
        if market:
            parts.append(f"рыночный ориентир — {market:.0f} ₽ за заявку")
        parts.append("то есть столько же заявок можно получать дешевле, "
                     "перенаправив бюджет в работающую кампанию")
        out.append(dict(
            title="Заявки дороже, чем можно получить в своём аккаунте",
            detail=". ".join(parts) + "",
            evidence={"цена заявки": round(price, 2),
                      "лучшая цена в аккаунте": round(best_price, 2),
                      "лучшая кампания": best_campaign.get("Name"),
                      "рыночная цена (реестр)": market,
                      "расход": eco.get("расход"),
                      "целевые заявки": eco.get("конверсии"),
                      "источник конверсий": eco.get("источник")},
            fix=f"Перенаправить бюджет в «{(best_campaign.get('Name') or '')[:40]}» "
                f"или перевести эту кампанию на автостратегию с целевой ценой конверсии",
            money=eco.get("расход"),
            **_meta(c),
        ))
    return out


@register("DIRECT.STRATEGY_CPA_INCONSISTENT", "direct", "info",
          description="Целевые цены конверсии в стратегиях различаются в разы")
def check_strategy_cpa_consistency(ctx):
    """
    Целевые цены конверсии в стратегиях должны опираться на один ориентир.

    Когда в одной кампании стоит 150 ₽, а в другой 4 000 ₽, часть кампаний
    покупает заявки вслепую: цифра в стратегии — это буквально то, за сколько
    алгоритм согласен покупать результат.
    """
    strategies = ctx.data.get("strategies")
    if strategies is None:
        return []

    values = []
    for info in (strategies or {}).values():
        if info.get("state") == "ARCHIVED":
            continue
        for scope, scope_info in (info.get("scopes") or {}).items():
            cpa = scope_info.get("cpa")
            if scope_info.get("type") in access.AUTO_TYPES and cpa:
                values.append((float(cpa), info, scope))
    if len(values) < 2:
        return []

    low = min(values, key=lambda v: v[0])
    high = max(values, key=lambda v: v[0])
    if high[0] <= low[0] * 2:
        return []

    target = ctx.target_cpa
    market = _market_cpa(ctx)
    plan_bits = []
    if target:
        plan_bits.append(f"план по проекту — {target:.0f} ₽")
    if market:
        plan_bits.append(f"рынок — {market:.0f} ₽")

    return [dict(
        title="Целевые цены конверсии в стратегиях различаются в разы",
        detail=f"Самая низкая цель — {low[0]:.0f} ₽ "
               f"(«{(low[1].get('name') or '')[:40]}»), самая высокая — "
               f"{high[0]:.0f} ₽ («{(high[1].get('name') or '')[:40]}»). "
               + (", ".join(plan_bits) + ". " if plan_bits else "")
               + "Так кампании торгуются за результат по разным правилам, "
                 "и сравнивать их эффективность нельзя",
        evidence={"целевые цены": sorted({v[0] for v in values}),
                  "кампаний с ценой в стратегии": len(values)},
        fix="Привести целевые цены конверсии к единому ориентиру "
            "(целевой CPA проекта или рыночная цена заявки)",
        object_type="account", object_id=ctx.account,
    )]

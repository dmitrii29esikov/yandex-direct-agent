"""
Kross-konturnye proverki — samaya tsennaya gruppa dlya konversij.

Zdes' svyazyvayutsya tri kontura: kampaniya Direkta -> schetchik Metriki ->
celi -> kontejner Tag Managera. Imenno zdes' nahoдyat'sya prichiny
"rashod est', konversij net".
"""

from .base import register


def _campaign_meta(campaign):
    return {"object_type": "campaign", "object_id": str(campaign.get("Id")),
            "object_name": campaign.get("Name")}


@register("CROSS.UNLINKED_COUNTERS", "cross", "info",
          description="В Метрике видны счётчики, не привязанные в реестре")
def check_unlinked_counters(ctx):
    linked = set(str(c) for c in (ctx.data.get("linked_counter_ids") or []))
    counters = ctx.data.get("counters") or []
    if not counters:
        return []
    # counters uzhe otfiltrovany po reestru, poetomu smotrim polnyj spisok
    all_counters = ctx.data.get("all_counters") or []
    if not all_counters or not linked:
        return []

    extra = [c for c in all_counters if str(c.get("id")) not in linked]
    if not extra:
        return []
    return [dict(
        title="Есть непривязанные счётчики Метрики",
        detail=f"Доступно {len(extra)} счётчиков, которых нет в реестре аккаунта",
        evidence={"примеры": [{"id": c.get("id"), "name": c.get("name"),
                               "owner_login": c.get("owner_login")}
                              for c in extra[:20]]},
        fix="Привязать нужные счётчики: link_account_data(account=..., counter_ids=[...])",
        object_type="account", object_id=ctx.account,
    )]


@register("CROSS.GOALS_MISSING_IN_DIRECT", "cross", "warning",
          description="Цель Метрики не передана в кампанию Директа")
def check_goals_missing_in_direct(ctx):
    counters = [c for c in (ctx.data.get("counters") or []) if c.get("id")]
    if not counters:
        return []

    counter_ids = {int(c["id"]) for c in counters if str(c.get("id", "")).isdigit()}
    if not counter_ids:
        return []

    out = []
    checked_any = False
    for campaign in ctx.data.get("campaigns") or []:
        if campaign.get("State") == "ARCHIVED":
            continue
        raw = campaign.get("CounterIds")
        if raw is None:
            continue          # pole nedostupno — chestno propuskaem
        checked_any = True
        items = []
        if isinstance(raw, dict):
            items = raw.get("Items") or []
        elif isinstance(raw, list):
            items = raw
        items = {int(i) for i in items if str(i).isdigit()}

        if not (items & counter_ids):
            out.append(dict(
                title="Цель Метрики не передана в кампанию",
                detail="К кампании не привязан счётчик Метрики: автостратегия "
                       "не видит конверсии и не может обучаться",
                evidence={"счётчики кампании": sorted(items),
                          "счётчики аккаунта": sorted(counter_ids)},
                fix="Привязать счётчик и выбрать цель конверсии в настройках стратегии",
                blocking=True,
                **_campaign_meta(campaign),
            ))

    if not checked_any:
        ctx.note("CROSS.GOALS_MISSING_IN_DIRECT: поле CounterIds недоступно, проверка пропущена")
    return out


@register("CROSS.DIRECT_CLIENTS_AVAILABLE", "cross", "info",
          description="Владелец счётчика даёт доступ к клиентам Директа")
def check_direct_clients(ctx):
    clients = ctx.data.get("direct_clients") or []
    if not clients:
        return []
    return [dict(
        title="Доступны клиенты Директа владельца счётчика",
        detail="Метрика вернула клиентов Директа с логинами главных представителей — "
               "это логины для Client-Login при работе через агентский доступ",
        evidence={"клиенты": clients[:20]},
        fix="Для агентского режима указать client_login из chief_login",
        object_type="account", object_id=ctx.account,
    )]


@register("CROSS.CONTAINER_COUNTER_MISMATCH", "cross", "warning",
          description="Контейнер Tag Manager привязан к чужому счётчику")
def check_container_counter(ctx):
    counter_ids = {str(c.get("id")) for c in (ctx.data.get("counters") or [])}
    out = []
    for cid, buckets in (ctx.data.get("ytm") or {}).items():
        meta = buckets.get("meta")
        if not meta:
            continue
        container_counter = meta.get("counter_id")
        if container_counter is None:
            out.append(dict(
                title="Контейнер Tag Manager не привязан к счётчику",
                detail="Без привязки к счётчику разметка не передаёт данные в Метрику",
                evidence={"container_id": cid},
                fix="Привязать контейнер к счётчику в интерфейсе Tag Manager",
                fixable="ytm_ui",
                object_type="container", object_id=str(cid),
            ))
        elif counter_ids and str(container_counter) not in counter_ids:
            out.append(dict(
                title="Контейнер привязан к счётчику другого аккаунта",
                detail=f"Контейнер связан со счётчиком {container_counter}, "
                       f"которого нет у этого аккаунта",
                evidence={"container_id": cid, "counter_id": container_counter,
                          "счётчики аккаунта": sorted(counter_ids)},
                fix="Проверить, что контейнер и счётчик относятся к одному проекту",
                object_type="container", object_id=str(cid),
            ))
    return out

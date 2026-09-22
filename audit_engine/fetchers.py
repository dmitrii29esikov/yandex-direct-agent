"""
Sborschik dannyh dlya audita.

Vse vneshnie vyzovy zdes'. Chtoby ni odin sdohnuvshij zapros ne lomal audit,
lyubaya oshibka popadaet v ctx.errors i dayot sektsiyu "Ne provereno".

Poleznoe svojstvo: esli API otvechaet oshibkoj 8000 ("neizvestnoe pole"),
my sami ubiraem lishnie polya iz zaprosa i povtoryaem. Spasatel'naya setka
protiv izmenenij v API.
"""

import logging

import access

log = logging.getLogger("audit.fetch")

# --- Nabory polej. Pri oshibke 8000 lishnie polya ubiraem avtomaticheski.
CAMPAIGN_FIELDS = [
    "Id", "Name", "Type", "State", "Status", "StatusPayment",
    "StatusClarification", "StartDate", "EndDate", "DailyBudget",
    "NegativeKeywords", "TimeTargeting",
]
ADGROUP_FIELDS = ["Id", "Name", "CampaignId", "Status", "Type", "Subtype",
                  "NegativeKeywords", "RegionIds"]
AD_FIELDS = ["Id", "AdGroupId", "CampaignId", "State", "Status",
             "StatusClarification", "Type"]
TEXT_AD_FIELDS = ["Title", "Title2", "Text", "Href", "DisplayUrlPath",
                  "SitelinkSetId", "AdExtensions", "VCardId", "AdImageHash"]
KEYWORD_FIELDS = ["Id", "AdGroupId", "CampaignId", "Keyword", "State", "Status"]

PAGE_SIZE = 10000
# Direct API prinimaet ne bolee 10 identifikatorov v SelectionCriteria
# (Ids / CampaignIds / AdGroupIds) za odin zapros. 27 kampanij v odnom zaprose
# dayut oshibku 4001 "Prevysheno dopustimoe kolichestvo identifikatorov".
ID_CHUNK = 10
MAX_HEAL_ROUNDS = 4


def _valid_values(detail: str):
    """Vytaskivaem spisok dopustimyh znachenij iz teksta oshibki 8000."""
    marker = "ожидается одно из значений:"
    if marker not in detail:
        return None
    tail = detail.split(marker, 1)[1]
    values = {v.strip() for v in tail.split(",") if v.strip()}
    return values or None


def _heal(params: dict, detail: str):
    """Ubiraem iz zaprosa polya, kotorye API ne znaet. None — hechit' nechego."""
    valid = _valid_values(detail)
    if not valid:
        return None
    healed = dict(params)
    changed = False
    for key in ("FieldNames", "TextAdFieldNames"):
        fields = healed.get(key)
        if isinstance(fields, list):
            kept = [f for f in fields if f in valid]
            if len(kept) != len(fields):
                healed[key] = kept
                changed = True
    return healed if changed else None


def _post(client, service, params, ctx, label):
    """POST s samovosstanovleniem nabora polej."""
    result = client.post(service, "get", params)
    for _ in range(MAX_HEAL_ROUNDS):
        if not (isinstance(result, dict) and result.get("error")):
            return result
        detail = str(result.get("error_text") or result.get("error"))
        if "8000" not in detail:
            break
        healed = _heal(params, detail)
        if not healed:
            break
        params = healed
        result = client.post(service, "get", params)

    if isinstance(result, dict) and result.get("error"):
        ctx.add_error(label, str(result.get("error_text") or result.get("error")))
        return None
    return result


def _paged(client, service, fields, ctx, label, key, criteria=None, extra=None,
           page_size=PAGE_SIZE):
    """Posledovatel'no vygruzhaem vse ob'ekty servisa (s razbivkoj po kriteriyu)."""
    items: list = []
    conditions = criteria or [{}]

    for cond in conditions:
        offset = 0
        for _ in range(500):        # predohranitel' ot beskonechnogo tsikla
            params = {
                "SelectionCriteria": dict(cond),
                "FieldNames": fields,
                "Page": {"Limit": page_size, "Offset": offset},
            }
            if extra:
                params.update(extra)

            result = _post(client, service, params, ctx, label)
            if not result:
                return None

            chunk = ((result.get("result") or {}).get(key)) or []
            items.extend(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size

    return items


def _num(value) -> float:
    """Chisla iz TSV-otcheta: '', '-', probely, nezlomimye probely, zapyataya."""
    if value is None:
        return 0.0
    s = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if s in ("", "-", "--"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _chunks(ids, size=ID_CHUNK):
    return [{"CampaignIds": ids[i:i + size]} for i in range(0, len(ids), size)]


def fetch_direct(ctx):
    """Kampanii, gruppy, obyavleniya i frazy."""
    client = access.direct(ctx.account)

    campaigns = _paged(client, "campaigns", CAMPAIGN_FIELDS, ctx,
                       "campaigns.get", "Campaigns")
    ctx.data["campaigns"] = campaigns

    ids = [c["Id"] for c in (campaigns or []) if c.get("Id")]
    if not ids:
        # Bez kampanij zaprashivat' gruppy/obyavleniya/frazy nel'zya:
        # API trebuet SelectionCriteria s CampaignIds.
        ctx.data.setdefault("adgroups", None)
        ctx.data.setdefault("ads", None)
        ctx.data.setdefault("keywords", None)
        ctx.note("Кампании не получены или отсутствуют — группы, объявления и фразы"
                 " не проверялись")
    else:
        criteria = _chunks(ids)
        ctx.data["adgroups"] = _paged(client, "adgroups", ADGROUP_FIELDS, ctx,
                                      "adgroups.get", "AdGroups", criteria=criteria)
        ctx.data["ads"] = _paged(client, "ads", AD_FIELDS, ctx, "ads.get", "Ads",
                                 criteria=criteria,
                                 extra={"TextAdFieldNames": TEXT_AD_FIELDS})
        ctx.data["keywords"] = _paged(client, "keywords", KEYWORD_FIELDS, ctx,
                                      "keywords.get", "Keywords", criteria=criteria)

    ctx.data["units"] = client.units_info()
    ctx.data["direct_mode"] = access.direct_mode(ctx.account)


def fetch_stats(ctx, date_range: str):
    """Statistika po kampaniyam cherez Reports API."""
    from tools.reports import get_campaign_stats

    data = get_campaign_stats(
        date_range=date_range,
        fields=["CampaignId", "CampaignName", "Impressions", "Clicks",
                "Ctr", "Cost", "AvgCpc", "Conversions", "CostPerConversion"],
        account=ctx.account,
    )
    if isinstance(data, dict) and data.get("error"):
        ctx.add_error("reports/campaigns", str(data["error"]))
        ctx.data["stats"] = None
        return

    stats = {}
    for row in data.get("rows", []):
        cid = row.get("CampaignId")
        if cid is None:
            continue
        stats[str(cid)] = {
            "impressions": _num(row.get("Impressions")),
            "clicks": _num(row.get("Clicks")),
            "cost": _num(row.get("Cost")),
            "conversions": _num(row.get("Conversions")),
            "cpa": _num(row.get("CostPerConversion")) or None,
        }
    ctx.data["stats"] = stats


def fetch_metrica(ctx):
    """Schetchiki i ih celi. Predstvitel'skij dostup — cherez ulogin."""
    counters_data = access.metrica_get(ctx.account, "/management/v1/counters")
    if isinstance(counters_data, dict) and counters_data.get("error"):
        ctx.add_error("metrica/counters", counters_data["error"])
        ctx.data["counters"] = None
        ctx.data["goals"] = None
        return

    linked = access.metrica_counter_ids(ctx.account)
    all_counters = (counters_data or {}).get("counters", [])

    ctx.data["linked_counter_ids"] = linked
    ctx.data["all_counters"] = all_counters

    counters = all_counters
    if linked:
        counters = [c for c in counters if c.get("id") in linked]

    ctx.data["counters"] = counters
    ctx.data["goals"] = {}
    for c in counters:
        cid = c.get("id")
        goals = access.metrica_get(ctx.account, f"/management/v1/counter/{cid}/goals")
        if isinstance(goals, dict) and goals.get("error"):
            ctx.add_error(f"metrica/goals/{cid}", goals["error"])
            continue
        ctx.data["goals"][cid] = (goals or {}).get("goals", [])


def fetch_ytm(ctx):
    """Tegi, triggery, peremennye i metadannye kontеjnerov."""
    ctx.data["ytm"] = {}
    for cid in access.ytm_container_ids(ctx.account):
        buckets = {}

        meta = access.ytm_get(ctx.account, f"container/{cid}")
        if isinstance(meta, dict) and meta.get("error"):
            ctx.add_error(f"ytm/container/{cid}", meta["error"])
            buckets["meta"] = None
        else:
            buckets["meta"] = (meta or {}).get("container", {})

        for name, key in (("tags", "tags"), ("triggers", "triggers"),
                          ("variables", "variables")):
            data = access.ytm_get(ctx.account, f"container/{cid}/{name}")
            if isinstance(data, dict) and data.get("error") and key not in data:
                ctx.add_error(f"ytm/{name}/{cid}", data["error"])
                buckets[name] = None
            else:
                buckets[name] = (data or {}).get(key, [])

        ctx.data["ytm"][cid] = buckets


def fetch_campaign_clients(ctx):
    """Svyazka Direct <-> Metrika: klienty Direct s ih chief_login."""
    counter_ids = access.metrica_counter_ids(ctx.account) or [
        c.get("id") for c in (ctx.data.get("counters") or []) if c.get("id")]
    if not counter_ids:
        ctx.note("metrica/clients: нет счётчиков для запроса — список клиентов Директа не получен")
        ctx.data["direct_clients"] = None
        return

    data = access.metrica_get(ctx.account, "/management/v1/clients",
                              {"counters": ",".join(str(c) for c in counter_ids)})
    if isinstance(data, dict) and data.get("error"):
        ctx.add_error("metrica/clients", data["error"])
        ctx.data["direct_clients"] = None
        return
    ctx.data["direct_clients"] = (data or {}).get("clients", [])

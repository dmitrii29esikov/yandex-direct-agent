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
import ytm_config

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


def fetch_strategies(ctx):
    """
    Strategii, prioritetnye tseli i schetchiki kampanij.

    Otdel'nyj shag, a ne chast' fetch_direct: esli Direct perestanet prinimat'
    dopolnitel'nye nabory polej, my poteryaem tol'ko proverki strategij,
    a ne ves audit.
    """
    campaigns = ctx.data.get("campaigns")
    if campaigns is None:
        ctx.data["strategies"] = None
        ctx.data["priority_goals"] = None
        ctx.note("Стратегии не запрошены: список кампаний не получен")
        return

    ids = [c.get("Id") for c in campaigns if c.get("Id")]
    data = access.campaign_strategies(ctx.account, ids)
    if data.get("error"):
        ctx.add_error("direct/strategies", data["error"])
        ctx.data["strategies"] = None
        strategies = {}
    else:
        strategies = data.get("campaigns") or {}
        ctx.data["strategies"] = strategies
        ctx.note(f"Стратегии прочитаны через campaigns.get: {data.get('requests')} "
                 f"запроса, поле {data.get('subfields_param')}")
        if not strategies:
            ctx.note("Кампаний для чтения стратегий нет")

    resolved = access.priority_goals_resolved(ctx.account, strategies=strategies)
    ctx.data["priority_goals"] = resolved
    source = resolved.get("source")
    if source == "api":
        ctx.note("Приоритетные цели кампаний прочитаны из API (поле PriorityGoals)")
    elif source == "registry":
        ctx.note("Приоритетные цели взяты из реестра: API их не отдал "
                 f"({resolved.get('error') or 'нет данных'})")
    elif source == "mixed":
        ctx.note("Приоритетные цели: источник — API, но есть расхождения с "
                 f"реестром по {len(resolved.get('mismatch') or {})} кампаниям "
                 "(см. DIRECT.GOALS_REGISTRY_DRIFT)")
    else:
        ctx.note("Приоритетные цели не найдены: ни API, ни реестр их не отдают")


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


def fetch_stats_targeted(ctx):
    """
    Целевые конверсии по приоритетным целям кампаний: во сколько обходится заявка.

    Запрашиваем СТРОГО по одной кампании с её собственными целями. Почему не
    одним отчётом на весь аккаунт: если передать цели разных кампаний сразу,
    Direct раскидывает значения по колонкам целей непредсказуемо — в строке
    кампании появлялись конверсии чужих целей (проверено 22.09.2026: у LoveScore
    в отчёте оказалась цель 13 из OUTLETIKA, а у неё самой нет такой цели).
    Расход берём из обычной статистики, чтобы не удваивать его.
    """
    goals_by_campaign = (ctx.data.get("priority_goals") or {}).get("goals") or {}
    if not goals_by_campaign or ctx.data.get("campaigns") is None:
        ctx.data["stats_targeted"] = None
        if ctx.data.get("campaigns") is not None:
            ctx.note("Целевые заявки не посчитаны: приоритетные цели не прочитаны")
        return

    from . import direct_reports as dr

    stats = ctx.data.get("stats") or {}
    result = {}
    errors = 0
    for campaign in ctx.data.get("campaigns") or []:
        cid = campaign.get("Id")
        try:
            goals = goals_by_campaign.get(int(cid)) or []
        except (TypeError, ValueError):
            goals = []
        cost = (stats.get(str(cid)) or {}).get("cost") or 0
        if not goals or not cost:
            result[str(cid)] = {
                "conversions": 0, "cpa": None, "cost": cost, "goals": goals,
                "reason": "нет целей" if not goals else "нет расхода",
            }
            continue

        found = 0
        failed = False
        for start in range(0, len(goals), 10):
            chunk = goals[start:start + 10]
            data = dr.run_report(ctx.account, "CUSTOM_REPORT",
                                 ["CampaignId", "Conversions"],
                                 period=ctx.date_range,
                                 filters=dr._campaign_filter([cid]), goals=chunk)
            if data.get("error"):
                ctx.add_error(f"direct/целевые конверсии/{cid}", data["error"])
                errors += 1
                failed = True
                break
            # При передаче Goals Direct возвращает разбивку по целям:
            # колонки «Conversions_<цель>_<модель>», а не одну «Conversions».
            columns = [c for c in (data.get("columns") or [])
                       if c.startswith("Conversions")]
            for row in data["rows"]:
                found += sum(dr.num(row.get(col)) for col in columns)
        if failed:
            continue

        result[str(cid)] = {
            "conversions": found,
            "cpa": round(cost / found, 2) if found else None,
            "cost": cost,
            "goals": goals,
        }

    ctx.data["stats_targeted"] = result
    ctx.data["stats_targeted_goals"] = sorted(
        {int(g) for items in goals_by_campaign.values() for g in items})
    ctx.note(f"Целевые заявки посчитаны по приоритетным целям: {len(result)} кампаний"
             + (f", ошибок запросов: {errors}" if errors else ""))


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
    _fetch_ytm_config(ctx)
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


def _fetch_ytm_config(ctx):
    """
    Publichnyj ytm-config po kazhdomu schetchiku: token ne nuzhen.

    Daet otvet na vopros, kotoryj API YTM ne umеет: kakie kontejnery voobsche
    est' u schetchikov i napolneny li oni. Kontejner mozhet byt' vklyuchen i
    pustym — togda razmetka nichego ne sobiraet, no eto ne polomka, a
    nazavershennaya nastrojka.
    """
    counters = ctx.data.get("counters")
    if counters is None:
        ctx.data["ytm_config"] = None
        return

    result = {}
    for counter in counters:
        counter_id = counter.get("id")
        if not counter_id:
            continue
        try:
            result[counter_id] = ytm_config.summary(counter_id)
        except Exception as e:
            ctx.add_error(f"ytm-config/{counter_id}", f"{type(e).__name__}: {e}")

    ctx.data["ytm_config"] = result


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


def fetch_deep(ctx, period: str):
    """
    Tяжёлые отчёты: sravnenie periodov, poisк, gruppy, ploshchadki, matritsa tselej.

    Kazhdyj otchet otdel'no i so svoim perehvatom oshibki: sboj odnogo otcheta
    ne dolzhen lomat' ves' audit. Esli otchet ne poluchen, sootvetstvuyushchee
    pole stanovitsya None — i proverka chestno propuskaetsya, a ne vydaet
    lozhnuyu nahodku.
    """
    from . import deltas as _deltas
    from . import direct_reports as dr

    keys = ("deltas", "search_queries", "adgroups_perf", "placements",
            "goal_matrix", "goal_names")
    campaigns = ctx.data.get("campaigns")
    if campaigns is None:
        for key in keys:
            ctx.data[key] = None
        ctx.note("Дополнительные отчёты не запрошены: список кампаний не получен")
        return

    ids = [c.get("Id") for c in campaigns if c.get("Id")]

    data = _deltas.campaign_deltas(ctx.account, period, ids)
    if data.get("error"):
        ctx.add_error("deltas", data["error"])
        ctx.data["deltas"] = None
    else:
        ctx.data["deltas"] = data

    for key, fn in (("search_queries", dr.search_queries),
                    ("adgroups_perf", dr.adgroup_performance),
                    ("placements", dr.placements)):
        result = fn(ctx.account, period, ids)
        if result.get("error"):
            ctx.add_error(key, result["error"])
            ctx.data[key] = None
        else:
            ctx.data[key] = result.get("rows") or []

    # Otchety s uchetom prioritetnyh tselej kampanij. Bez nih analiz
    # «den'gi v ploshchadki bez celevyh» schitaet konversii po vsem tselyam.
    resolved = ctx.data.get("priority_goals")
    if resolved is None:
        resolved = access.priority_goals_resolved(ctx.account)
    goal_map = resolved.get("goals") or {}
    if goal_map:
        for key, fn in (("placements_targeted", dr.placements_goal_aware),
                        ("adgroups_targeted", dr.adgroups_goal_aware)):
            result = fn(ctx.account, period, goal_map, ids)
            if result.get("error"):
                ctx.add_error(key, result["error"])
                ctx.data[key] = None
            else:
                ctx.data[key] = result.get("rows") or []
    else:
        ctx.data["placements_targeted"] = None
        ctx.data["adgroups_targeted"] = None
        ctx.note("Приоритетные цели не получены ни через API, ни из реестра — "
                 "площадки и группы без целевых конверсий не проверялись. "
                 "Проверьте, что в стратегиях кампаний выбраны цели Метрики")

    # Imena tselej — nuzhny, chtoby v otchete pisat' nazvanie, a ne nomer.
    names = {}
    for goals in (ctx.data.get("goals") or {}).values():
        for goal in goals or []:
            if goal.get("id"):
                names[int(goal["id"])] = goal.get("name")
    ctx.data["goal_names"] = names

    goals_all = []
    for goals in (ctx.data.get("goals") or {}).values():
        for goal in goals or []:
            gid = goal.get("id")
            if gid and int(gid) not in goals_all:
                goals_all.append(int(gid))
    if not goals_all:
        ctx.data["goal_matrix"] = None
        ctx.note("Цели Метрики не получены — проверка неработающих целей пропущена")
        return

    merged = {"goals": [], "by_campaign": {}, "dead_goals": []}
    for start in range(0, len(goals_all), 10):
        chunk = goals_all[start:start + 10]
        result = dr.goal_matrix(ctx.account, "LAST_30_DAYS", chunk, ids)
        if result.get("error"):
            ctx.add_error(f"goal_matrix[{start}]", result["error"])
            continue
        merged["goals"].extend(result.get("goals") or [])
        for cid, bucket in (result.get("by_campaign") or {}).items():
            merged["by_campaign"].setdefault(cid, {}).update(bucket)

    if not merged["goals"]:
        ctx.data["goal_matrix"] = None
        return
    merged["dead_goals"] = [
        g for g in merged["goals"]
        if sum(b.get(g, 0) for b in merged["by_campaign"].values()) == 0]
    ctx.data["goal_matrix"] = merged

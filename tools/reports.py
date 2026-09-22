from mcp_instance import mcp, api_client
# Otchety po kampaniyam Yandex Direct (Reports API v5).
# Kommentarii translitom, chtoby ne bylo krakozyabr v Windows-1251.

import time
from datetime import date, timedelta

import requests

from mcp_instance import mcp, api_client


# Polya, kotorye tochno est' v CAMPAIGN_PERFORMANCE_REPORT.
ALLOWED_FIELDS = [
    "CampaignId", "CampaignName", "Impressions", "Clicks",
    "Ctr", "Cost", "AvgCpc",
    "Conversions", "CostPerConversion", "ConversionRate",
]

# Cpa/Revenue dostupny tolko v CUSTOM_REPORT.
CUSTOM_ONLY_FIELDS = ["Cpa", "Revenue"]

# Znacheniya DateRangeType iz dokumentacii Yandex Direct Reports API.
ALLOWED_DATE_RANGES = [
    "TODAY", "YESTERDAY",
    "LAST_3_DAYS", "LAST_5_DAYS", "LAST_7_DAYS", "LAST_14_DAYS",
    "LAST_30_DAYS", "LAST_90_DAYS", "LAST_365_DAYS",
    "THIS_WEEK_MON_TODAY", "THIS_WEEK_SUN_TODAY",
    "LAST_WEEK", "LAST_BUSINESS_WEEK", "LAST_WEEK_SUN_SAT",
    "THIS_MONTH", "LAST_MONTH",
    "ALL_TIME", "AUTO",
]

import access

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"


def _decode(response) -> str:
    """
    Yandex otdaet JSON i TSV v UTF-8, no ne vsegda ukazyvaet charset v zagolovke.
    requests v takom sluchae ugadyvaet kodirovku, i v tekstah oshibok i v
    nazvaniyah kampanij poluchaetsya krakozyabra. Poetomu dekodiruem yavno.
    """
    return response.content.decode("utf-8", errors="replace")


def _parse_tsv(text: str) -> dict:
    """Razbor TSV-otcheta v dict so spiskom strok."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return {"count": 0, "columns": [], "rows": []}
    header = lines[0].split("\t")
    rows = [dict(zip(header, l.split("\t"))) for l in lines[1:]]
    return {"count": len(rows), "columns": header, "rows": rows}


def _resolve_date_range(date_range: str):
    """
    Vozvrashchaem (date_range_type, date_from_s, date_to_s, error_dict).

    - Dlya standartnyh periodov (LAST_7_DAYS i t.p.): date_range_type = sam
      period, a date_from_s / date_to_s = None (ih peredavat' nel'zya).
    - Dlya custom "YYYY-MM-DD,YYYY-MM-DD": date_range_type = "CUSTOM_DATE",
      date_from_s / date_to_s zapolneny.
    """
    # Custom period
    if "," in date_range:
        parts = [d.strip() for d in date_range.split(",", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None, None, None, {
                "error": f"Nekorrektnyj custom period: {date_range}. "
                         f"Ozhidaetsya 'YYYY-MM-DD,YYYY-MM-DD'",
            }
        return "CUSTOM_DATE", parts[0], parts[1], None

    # Standartnyj period
    if date_range not in ALLOWED_DATE_RANGES:
        return None, None, None, {
            "error": f"Nedopustimyj date_range: {date_range}",
            "allowed": ALLOWED_DATE_RANGES,
        }

    return date_range, None, None, None


@mcp.tool()
def get_campaign_stats(
    date_range: str = "LAST_7_DAYS",
    fields: list[str] | None = None,
    campaign_ids: list[int] | None = None,
    goal_ids: list[int] | None = None,
    cpa_goal_id: int | None = None,
    attribution_model: str | None = None,
    account: str | None = None,
    max_wait_seconds: int = 120,
) -> dict:
    """Statistika kampanij: pokazy, klik, rashod, CTR, CPC, konversii, CPA."""
    fields = fields or [
        "CampaignId", "CampaignName", "Impressions", "Clicks",
        "Ctr", "Cost", "AvgCpc",
        "Conversions", "CostPerConversion", "ConversionRate",
    ]

    allowed_all = ALLOWED_FIELDS + CUSTOM_ONLY_FIELDS
    bad = [f for f in fields if f not in allowed_all]
    if bad:
        return {"error": f"Nedopustimye polya: {bad}", "allowed": allowed_all}

    needs_custom = any(f in CUSTOM_ONLY_FIELDS for f in fields)
    report_type = "CUSTOM_REPORT" if needs_custom else "CAMPAIGN_PERFORMANCE_REPORT"

    date_range_type, date_from_s, date_to_s, err = _resolve_date_range(date_range)
    if err:
        return err

    effective_goals = list(goal_ids) if goal_ids else []
    if cpa_goal_id is not None:
        cpa_goal_id = int(cpa_goal_id)
        if cpa_goal_id not in effective_goals:
            effective_goals.append(cpa_goal_id)

    # API prinimaet ne bolee 10 tselej v odnom zaprose (oshibka 7000).
    if len(effective_goals) > 10:
        return {"error": f"Goals может содержать не более 10 элементов, "
                          f"передано {len(effective_goals)}. Разбейте на части."}

    # SelectionCriteria:
    # - Filter i Goals — vsegda mozhno.
    # - DateFrom/DateTo — TOLKO pri DateRangeType == "CUSTOM_DATE".
    selection = {}
    if campaign_ids:
        selection["Filter"] = [{
            "Field": "CampaignId",
            "Operator": "IN",
            "Values": [str(c) for c in campaign_ids],
        }]
    if date_range_type == "CUSTOM_DATE":
        selection["DateFrom"] = date_from_s
        selection["DateTo"] = date_to_s

    params = {
        "SelectionCriteria": selection,
        "FieldNames": fields,
        # Vazhno: Goals — tol'ko na verhnem urovne params. Vnutri SelectionCriteria
        # API ego ne prinimaet (oshibka 8000 «неизвестное поле Goals»), a molcha
        # podmenit' ne stanet: bez Goals konversii schitayutsya po vsem tselyam
        # kampanii, i CPA poluchaetsya smeshnym (naprimer 2,32 rub. vmesto 15 rub.).
        "Goals": [str(g) for g in effective_goals],
        "ReportName": f"report_{int(time.time())}",
        "ReportType": report_type,
        "DateRangeType": date_range_type,
        "Format": "TSV",
        "IncludeVAT": "YES",
        "IncludeDiscount": "NO",
    }
    if not effective_goals:
        params.pop("Goals")
    if attribution_model:
        params["AttributionModels"] = [attribution_model]

    try:
        headers = dict(access.direct(account).headers)
    except access.AccessError as e:
        return {"error": str(e)}
    headers.update({
        "processingMode": "auto",
        "skipReportHeader": "true",
        "skipColumnHeader": "false",
        "skipReportSummary": "true",
        "returnMoneyInMicros": "false",
    })

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        try:
            resp = requests.post(
                REPORTS_URL, json={"params": params},
                headers=headers, timeout=60,
            )
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

        if resp.status_code == 200:
            return _parse_tsv(_decode(resp))

        if resp.status_code in (201, 202):
            retry_in = int(resp.headers.get("retryIn", "5"))
            time.sleep(retry_in)
            continue

        return {"error": f"Reports API {resp.status_code}: {_decode(resp)[:500]}"}

    return {"error": "Timeout: otchet ne uspel podgotovit'sya"}
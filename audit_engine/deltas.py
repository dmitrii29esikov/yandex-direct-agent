"""
Сравнение периода с предыдущим: динамика и аномалии.

Отвечает на вопрос «что изменилось по деньгам и результату», которого не хватало
в нашем аудите. Без него отчёт показывает состояние на сейчас, но не тренд.
"""

import logging

from . import direct_reports as dr

log = logging.getLogger("audit.deltas")

FIELDS = ["CampaignId", "CampaignName", "Impressions", "Clicks", "Ctr", "Cost",
          "AvgCpc", "Conversions", "CostPerConversion"]

# Пороги аномалий. Подобраны так, чтобы не шуметь на нормальных колебаниях.
CPC_SURGE = 1.5          # рост цены клика в 1,5 раза
SPEND_SURGE = 2.0        # рост расхода вдвое
CONVERSION_DROP = 0.5    # падение конверсий вдвое
MIN_MONEY_TO_CARE = 300  # меньше 300 ₽ за период не считаем значимым


def _pct(before: float, after: float):
    """Изменение в процентах. None, если базы не было."""
    if before == 0:
        return None
    return (after - before) / before * 100.0


def _fmt(value):
    if value is None:
        return "—"
    return f"{value:+.0f}%"


def _collect(account, period, campaign_ids):
    data = dr.run_report(account, "CUSTOM_REPORT", FIELDS, period=period,
                         filters=dr._campaign_filter(campaign_ids))
    if data.get("error"):
        return data, {}
    result = {}
    for row in data["rows"]:
        result[str(row.get("CampaignId"))] = row
    return data, result


def campaign_deltas(account, period: str = "LAST_7_DAYS",
                    campaign_ids: list | None = None) -> dict:
    """
    Сравниваем период с предыдущим отрезком той же длины.

    Возвращает по каждой кампании обе стороны и изменения, плюс отдельный
    список аномалий — то, на что стоит посмотреть человеку.
    """
    prev_period = dr.previous_period(period)
    cur_data, current = _collect(account, period, campaign_ids)
    if cur_data.get("error"):
        return cur_data
    prev_data, previous = _collect(account, prev_period, campaign_ids)
    if prev_data.get("error"):
        return prev_data

    rows = []
    anomalies = []
    for cid, cur in current.items():
        before = previous.get(cid, {})
        metric = {
            "campaign_id": int(cid),
            "campaign_name": cur.get("CampaignName"),
            "cost_now": dr.num(cur.get("Cost")),
            "cost_before": dr.num(before.get("Cost")),
            "clicks_now": dr.num(cur.get("Clicks")),
            "clicks_before": dr.num(before.get("Clicks")),
            "cpc_now": dr.num(cur.get("AvgCpc")),
            "cpc_before": dr.num(before.get("AvgCpc")),
            "conversions_now": dr.num(cur.get("Conversions")),
            "conversions_before": dr.num(before.get("Conversions")),
        }
        metric["cost_pct"] = _pct(metric["cost_before"], metric["cost_now"])
        metric["clicks_pct"] = _pct(metric["clicks_before"], metric["clicks_now"])
        metric["cpc_pct"] = _pct(metric["cpc_before"], metric["cpc_now"])
        metric["conversions_pct"] = _pct(metric["conversions_before"],
                                         metric["conversions_now"])
        rows.append(metric)

        if metric["cost_now"] < MIN_MONEY_TO_CARE:
            continue
        if (metric["cpc_before"] and metric["cpc_now"] / metric["cpc_before"] >= CPC_SURGE
                and metric["clicks_now"] > 0):
            anomalies.append({
                "kind": "CPC_SURGE", "campaign_id": metric["campaign_id"],
                "campaign_name": metric["campaign_name"],
                "text": f"цена клика выросла в "
                        f"{metric['cpc_now'] / metric['cpc_before']:.1f} раза: "
                        f"{metric['cpc_before']:.2f} → {metric['cpc_now']:.2f} ₽",
            })
        if metric["cost_before"] and metric["cost_now"] / metric["cost_before"] >= SPEND_SURGE:
            anomalies.append({
                "kind": "SPEND_SURGE", "campaign_id": metric["campaign_id"],
                "campaign_name": metric["campaign_name"],
                "text": f"расход вырос в {metric['cost_now'] / metric['cost_before']:.1f} раза: "
                        f"{metric['cost_before']:.0f} → {metric['cost_now']:.0f} ₽",
            })
        if (metric["conversions_before"] > 0
                and metric["conversions_now"] <= metric["conversions_before"] * CONVERSION_DROP):
            anomalies.append({
                "kind": "CONVERSIONS_DROP", "campaign_id": metric["campaign_id"],
                "campaign_name": metric["campaign_name"],
                "text": f"конверсии упали: {metric['conversions_before']:.0f} → "
                        f"{metric['conversions_now']:.0f}",
            })

    totals = {
        "cost_now": sum(r["cost_now"] for r in rows),
        "cost_before": sum(r["cost_before"] for r in rows),
        "conversions_now": sum(r["conversions_now"] for r in rows),
        "conversions_before": sum(r["conversions_before"] for r in rows),
        "clicks_now": sum(r["clicks_now"] for r in rows),
        "clicks_before": sum(r["clicks_before"] for r in rows),
    }
    totals["cost_pct"] = _pct(totals["cost_before"], totals["cost_now"])
    totals["conversions_pct"] = _pct(totals["conversions_before"],
                                     totals["conversions_now"])
    totals["cpa_now"] = (totals["cost_now"] / totals["conversions_now"]
                         if totals["conversions_now"] else None)
    totals["cpa_before"] = (totals["cost_before"] / totals["conversions_before"]
                            if totals["conversions_before"] else None)

    rows.sort(key=lambda r: -r["cost_now"])
    return {"account": account, "period": period, "previous_period": prev_period,
            "totals": totals, "campaigns": rows, "anomalies": anomalies,
            "anomalies_count": len(anomalies)}


def render_deltas(data: dict) -> str:
    """Текстовая сводка динамики — для отчёта в чат."""
    if data.get("error"):
        return f"Сравнение периодов недоступно: {data['error']}"
    t = data["totals"]
    lines = [f"Период {data['period']} против {data['previous_period']}:", ""]
    lines.append(f"| Кампания | Расход | Δ | Клики | Δ | ₽/клик | Δ | Конв. | Δ |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for row in data["campaigns"]:
        if row["cost_now"] == 0 and row["cost_before"] == 0:
            continue
        lines.append(
            f"| {row['campaign_name']} | {row['cost_now']:.0f} ₽ | "
            f"{_fmt(row['cost_pct'])} | {row['clicks_now']:.0f} | "
            f"{_fmt(row['clicks_pct'])} | {row['cpc_now']:.2f} ₽ | "
            f"{_fmt(row['cpc_pct'])} | {row['conversions_now']:.0f} | "
            f"{_fmt(row['conversions_pct'])} |")
    lines.append("")
    lines.append(f"**Итого:** расход {t['cost_now']:.0f} ₽ ({_fmt(t['cost_pct'])}), "
                 f"клики {t['clicks_now']:.0f} ({_fmt(_pct(t['clicks_before'], t['clicks_now']))}), "
                 f"конверсии {t['conversions_now']:.0f} "
                 f"({_fmt(t['conversions_pct'])})")
    if t.get("cpa_now"):
        lines.append(f"Цена конверсии: "
                     f"{t['cpa_before']:.0f} → {t['cpa_now']:.0f} ₽"
                     if t.get("cpa_before") else
                     f"Цена конверсии: {t['cpa_now']:.0f} ₽")
    if data["anomalies"]:
        lines.append("")
        lines.append("**Аномалии:**")
        for item in data["anomalies"]:
            lines.append(f"- {item['campaign_name']}: {item['text']}")
    return "\n".join(lines)

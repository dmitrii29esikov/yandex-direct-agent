"""
Dviжok pravok: chto mozhno ispravit' avtomaticheski.

Princip bezopasnosti:
- ispravlyaem TOL'KO nahodki s fixable="auto";
- kazhdoe dejstvie opisyvaetsya zaranee (before/after) — poetomu vozmozhen dry-run;
- ni odna pravka ne delaetsya bez yavnogo confirm=True;
- vse pravki zapisyvayutsya v log s prepodnymi znacheniyami — poetomu est' otkat.
"""

import logging
from dataclasses import dataclass, field, asdict

log = logging.getLogger("audit.fixers")

# Limity Direkta dlya tekstovo-graficheskih obyavlenij.
TEXT_LIMITS = {"Title": 56, "Title2": 56, "Text": 81}

MICROS = 1_000_000      # Direct prinimaet den'gi v mikroedinicah


@dataclass
class Action:
    """Odnа konkretnaya pravka: chto imenno izmenitsya."""

    code: str
    summary: str
    service: str
    method: str
    payload: dict
    object_type: str = ""
    object_id: str = ""
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


FIXERS: list = []


def fixer(code: str, description: str = ""):
    """Dekorator: registriruet fikser dlya konkretnogo koda nahodki."""
    def deco(fn):
        FIXERS.append({"code": code, "description": description, "fn": fn})
        return fn
    return deco


def shorten(value: str, limit: int) -> str:
    """
    Sokraschaem po granitse slova, chtoby ne razorvat' tekst poseredine.

    Esli slovo odno i dlinnee limita — zhestko obrezaem.
    """
    value = str(value).strip()
    if len(value) <= limit:
        return value
    cut = value[:limit].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut or value[:limit]


# --------------------------------------------------------------------------
# Fiksery
# --------------------------------------------------------------------------

@fixer("DIRECT.TEXT_LENGTH", "Сокращение заголовков и текстов до лимитов Директа")
def fix_text_length(ctx, findings):
    ads = {str(a.get("Id")): a for a in (ctx.data.get("ads") or [])}
    actions = []
    seen = set()

    for finding in findings:
        for example in finding.evidence.get("примеры", []):
            ad_id = str(example.get("ad_id"))
            if not ad_id or ad_id in seen:
                continue
            seen.add(ad_id)

            ad = ads.get(ad_id)
            if not ad:
                continue
            text_ad = ad.get("TextAd") or {}

            new_fields = {}
            before = {}
            for field_name, limit in TEXT_LIMITS.items():
                old = text_ad.get(field_name)
                if old and len(str(old)) > limit:
                    new_value = shorten(old, limit)
                    new_fields[field_name] = new_value
                    before[field_name] = old
            if not new_fields:
                continue

            actions.append(Action(
                code="DIRECT.TEXT_LENGTH",
                summary=f"Сократить тексты объявления {ad_id}",
                service="ads",
                method="update",
                payload={"Ads": [{"Id": int(ad_id), "TextAd": new_fields}]},
                object_type="ad",
                object_id=ad_id,
                before=before,
                after=new_fields,
            ))
    return actions


@fixer("DIRECT.NO_DAILY_BUDGET", "Простановка дневного бюджета из реестра")
def fix_daily_budget(ctx, findings):
    """
    Znachenie berem iz reestra: account.direct.daily_budget (v rublyah).

    Esli ono ne zadano — nichego ne delaem i chestno ob etom soobschaem:
    pridumyvat' byudzhet za klienta nel'zya.
    """
    budget = (ctx.record.get("direct") or {}).get("daily_budget")
    if not budget:
        ctx.note("daily_budget не задан в реестре — дневной бюджет не проставляем")
        return []
    if str(budget).lower() == "distributed":
        amount, mode = None, "DISTRIBUTED"
    else:
        amount, mode = int(float(budget) * MICROS), "STANDARD"

    actions = []
    for finding in findings:
        if finding.object_type != "campaign" or not finding.object_id:
            continue
        daily = {"Mode": mode}
        if amount is not None:
            daily["Amount"] = amount
        actions.append(Action(
            code="DIRECT.NO_DAILY_BUDGET",
            summary=f"Задать дневной бюджет кампании {finding.object_id}: {budget} ₽",
            service="campaigns",
            method="update",
            payload={"Campaigns": [{"Id": int(finding.object_id), "DailyBudget": daily}]},
            object_type="campaign",
            object_id=finding.object_id,
            before={"DailyBudget": None},
            after={"DailyBudget": daily},
        ))
    return actions

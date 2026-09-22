"""
Bazovye tipy dvizhka audita: Finding, Diagnosis, Ctx i reestr proverok.

Princip: kazhdaya proverka — nezavisimaya funktsiya, kotoraya poluchaet Ctx
i vozvraschaet spisok Finding. Odna upavshaya proverka ne lomaet ostatok.
"""

import logging
import math
from dataclasses import dataclass, field, asdict

log = logging.getLogger("audit")

# Baza prioriteta po urovnyu problemy.
SEVERITY_BASE = {"error": 40, "warning": 20, "info": 5}

# Stоimost' ispravleniya: avtomaticheskie fiksy prioritetnee ruchnyh.
FIX_COST = {"auto": 5, "manual": 0, "ytm_ui": -5}


@dataclass
class Finding:
    """Odnа nahodka: chto ne tak, na osnovanii chego i chto delat'."""

    code: str
    contour: str                 # direct | metrica | ytm | cross
    severity: str                # error | warning | info
    title: str
    detail: str = ""
    object_type: str = ""
    object_id: str = ""
    object_name: str = ""
    evidence: dict = field(default_factory=dict)
    fix: str = ""
    fixable: str = "manual"      # auto | manual | ytm_ui
    money: float | None = None
    blocking: bool = False       # blokiruet li eto drugie ispravleniya
    impact: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Diagnosis:
    """Nadgrupka nahodok: prichina + effekt + poryadok dejstvij."""

    key: str
    title: str
    impact: int
    severity: str
    account: str
    contour: str
    summary: str
    impact_text: str
    actions: list = field(default_factory=list)
    finding_codes: list = field(default_factory=list)
    object_ids: list = field(default_factory=list)
    money: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_impact(severity: str, money: float | None = None,
                   spend: float = 0.0, conversions: float = 0.0,
                   cpa: float | None = None, target_cpa: float | None = None,
                   fixable: str = "manual", blocking: bool = False) -> int:
    """
    Prioritet 0..100. Smysl: pokazat', chto pravit' PERVYM.

    Ves sostoit iz:
      - baza po urovnyu (error/warning/info);
      - skol'ko deneg pod riskom (logarifm rashoda);
      - est' li rashod bez konversij (samyj dorogoj signal);
      - blokiruet li nahodka drugie ispravleniya;
      - naskol'ko deshevo eto ispravit' (auto deshevle ruchnogo).
    """
    score = SEVERITY_BASE.get(severity, 5)

    # Dengi pod riskom: 10 000 rub. i vyshe dayut maksimum 32 balla.
    # Ves deneg samyj bol'shoj v formule — smysl audita v tom, skol'ko deneg
    # nа konu, a ne skol'ko tekhnicheskih nedochetov najdeno.
    if money:
        score += min(32, int(32 * min(1.0, math.log10(1 + max(money, 0)) / 4)))

    # Rashod bez konversij — samyj dorogoj signal v audite, no ego ves zavisit
    # ot masshtaba: kampaniya s 200 rub. i kampaniya s 5 000 rub. bez konversij —
    # problemy raznoj tyazhesti. Bez etoj gradatsii malen'kaya kampaniya
    # obgonyala by krupnuyu s dorogim konversiyami.
    if spend > 0 and conversions == 0:
        if spend >= 3000:
            score += 25
        elif spend >= 1000:
            score += 18
        elif spend >= 300:
            score += 10
        else:
            score += 4
    elif cpa and target_cpa and cpa > target_cpa:
        score += 15

    if blocking:
        score += 10

    score += FIX_COST.get(fixable, 0)

    return max(0, min(100, int(score)))


# --------------------------------------------------------------------------
# Reestr proverok
# --------------------------------------------------------------------------

@dataclass
class Check:
    code: str
    contour: str
    severity: str
    fn: callable
    description: str = ""
    fixable: str = "manual"


CHECKS: list = []


def register(code: str, contour: str, severity: str = "warning",
             description: str = "", fixable: str = "manual"):
    """Dekorator: registriruet proverku v reestre."""
    def deco(fn):
        CHECKS.append(Check(code=code, contour=contour, severity=severity,
                            fn=fn, description=description, fixable=fixable))
        return fn
    return deco


def run_checks(ctx) -> tuple:
    """
    Zapuskaem vse proverki. Vozvrashchaem (findings, oshibki_proverok).

    Nezavisimost': oshibka odnoj proverki popadaet v oshibki i ne meshaet ostal'nym.
    """
    findings: list = []
    failures: list = []

    for check in CHECKS:
        try:
            produced = check.fn(ctx) or []
        except Exception as e:  # odna proverka ne dolzhna lomat' ves' audit
            log.exception("Проверка %s упала", check.code)
            failures.append({"check": check.code, "error": f"{type(e).__name__}: {e}"})
            continue

        for item in produced:
            item.setdefault("severity", check.severity)
            item.setdefault("fixable", check.fixable)
            finding = Finding(
                code=item.pop("code", check.code),
                contour=check.contour,
                **item,
            )
            if not finding.impact:
                finding.impact = compute_impact(
                    severity=finding.severity,
                    money=finding.money,
                    spend=finding.evidence.get("расход", 0) or 0,
                    conversions=finding.evidence.get("конверсии", 0) or 0,
                    cpa=finding.evidence.get("CPA"),
                    target_cpa=finding.evidence.get("целевой CPA"),
                    fixable=finding.fixable,
                    blocking=finding.blocking,
                )
            findings.append(finding)

    findings.sort(key=lambda f: f.impact, reverse=True)
    return findings, failures

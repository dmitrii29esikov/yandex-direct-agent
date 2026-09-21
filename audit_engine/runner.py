"""
Zapusk audita: sobraem kontekst, tyanem dannye, gonyaem proverki, delaem diagnozy.
"""

import logging
from dataclasses import dataclass, field

import access
import accounts_store as store

from . import fetchers, report
from .base import CHECKS, run_checks
from .diagnose import diagnose

log = logging.getLogger("audit.runner")

DEFAULT_RANGE = "LAST_30_DAYS"


@dataclass
class Ctx:
    """Kontekst odnogo prohoda audita po odnomu akkauntu."""

    account: str
    date_range: str = DEFAULT_RANGE
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    target_cpa: float | None = None
    record: dict = field(default_factory=dict)

    def add_error(self, label: str, error):
        log.warning("[%s] %s: %s", self.account, label, error)
        self.errors.append({"label": label, "error": str(error)})

    def note(self, text: str):
        self.notes.append(text)


def _safe(ctx, label, fn, *args, **kwargs):
    """Lyubaya oshibka sbora dannyh popadaet v 'Ne provereno', a audit idet dal'she."""
    try:
        return fn(*args, **kwargs)
    except access.AccessError as e:
        ctx.add_error(label, str(e))
    except Exception as e:
        log.exception("Сбор данных упал: %s", label)
        ctx.add_error(label, f"{type(e).__name__}: {e}")
    return None


def build_context(account: str | None = None,
                  date_range: str = DEFAULT_RANGE) -> Ctx:
    """
    Sобираem kontekst akkaunta: reestr + dannye vseh tryoh konturov.

    Ispol'zuetsya i audитом, i dviжkom pravok — chtoby ne dublirovat' sbor.
    """
    name = store.resolve_or_default(account)
    ctx = Ctx(account=name, date_range=date_range)

    try:
        ctx.record = store.get(name)
    except KeyError:
        ctx.add_error("registry", f"Аккаунт '{name}' не найден в реестре. "
                                  f"Доступные: {', '.join(store.account_ids())}")
        return ctx

    ctx.target_cpa = (ctx.record.get("goals") or {}).get("target_cpa")

    _safe(ctx, "direct", fetchers.fetch_direct, ctx)
    _safe(ctx, "stats", fetchers.fetch_stats, ctx, date_range)
    _safe(ctx, "metrica", fetchers.fetch_metrica, ctx)
    _safe(ctx, "ytm", fetchers.fetch_ytm, ctx)
    _safe(ctx, "metrica/clients", fetchers.fetch_campaign_clients, ctx)
    return ctx


def run_audit(account: str | None = None, date_range: str = DEFAULT_RANGE,
              top: int = 10, save_files: bool = True) -> dict:
    """Polnyj audit odnogo akkaunta. Tol'ko chtenie."""
    ctx = build_context(account, date_range)
    if ctx.errors and ctx.errors[0]["label"] == "registry":
        return {"error": ctx.errors[0]["error"]}

    findings, failures = run_checks(ctx)
    diagnoses = diagnose(ctx.account, findings, target_cpa=ctx.target_cpa)

    result = {
        "account": ctx.account,
        "date_range": date_range,
        "checks_run": len(CHECKS),
        "findings_total": len(findings),
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "infos": sum(1 for f in findings if f.severity == "info"),
        "auto_fixable": sum(1 for f in findings if f.fixable == "auto"),
        "diagnoses": [d.to_dict() for d in diagnoses[:top]],
        "top_diagnosis": diagnoses[0].title if diagnoses else None,
        "not_checked": ctx.errors,
        "check_failures": failures,
        "notes": ctx.notes,
        "markdown": report.render_markdown(ctx, findings, diagnoses, top=top),
    }

    if save_files:
        result["xlsx"] = _safe(ctx, "report/xlsx", report.save_xlsx,
                               ctx, findings, diagnoses, failures)
        result["remediation"] = _safe(ctx, "report/remediation", report.save_remediation,
                                      ctx, diagnoses)

    return result

"""
Primenenie pravok: plan (dry-run) -> podtverzhdenie -> ispolnenie -> otkat.

Tri rezhima raboty s odnim i tem zhe planom:
  build_plan()  — tol'ko chitaet i opisyvaet, chto sdelal by;
  execute()     — vypolnyaet tol'ko posle confirm=True;
  save_log()    — pishet zhurnal s prepodnymi znacheniyami (osnova dlya otkata);
  rollback()    — vozvraschaet prepodnie znacheniya.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import access
from .base import run_checks
from .fixers import FIXERS, Action
from .runner import build_context

log = logging.getLogger("audit.apply")

AUDITS_DIR = Path(__file__).resolve().parent.parent / "data" / "audits"


def build_plan(ctx, findings, codes=None) -> dict:
    """Sobiraem spisok pravok. Ni odnogo izmeneniya v API."""
    by_code = {}
    for finding in findings:
        by_code.setdefault(finding.code, []).append(finding)

    actions: list = []
    problems: list = []

    for fx in FIXERS:
        if codes and fx["code"] not in codes:
            continue
        items = by_code.get(fx["code"], [])
        if not items:
            continue
        try:
            produced = fx["fn"](ctx, items) or []
        except Exception as e:
            log.exception("Фиксер %s упал", fx["code"])
            problems.append({"fixer": fx["code"], "error": f"{type(e).__name__}: {e}"})
            continue
        actions.extend(produced)

    auto_codes = {fx["code"] for fx in FIXERS}
    manual = {}
    for finding in findings:
        if finding.code in auto_codes:
            continue
        manual.setdefault(finding.code, {"code": finding.code,
                                        "count": 0,
                                        "fixable": finding.fixable})
        manual[finding.code]["count"] += 1

    return {
        "actions": actions,
        "actions_count": len(actions),
        "problems": problems,
        "manual": sorted(manual.values(), key=lambda m: -m["count"]),
        "fixers_available": [{"code": fx["code"], "description": fx["description"]}
                             for fx in FIXERS],
    }


def execute(ctx, actions) -> list:
    """Vypolnyaem pravki. Vyzvat' tol'ko posle yavnogo podtverzhdeniya."""
    try:
        client = access.direct(ctx.account)
    except access.AccessError as e:
        return [{"ok": False, "error": str(e)}]

    results = []
    for action in actions:
        response = client.post(action.service, action.method, action.payload)
        if isinstance(response, dict) and response.get("error"):
            results.append({
                "ok": False,
                "action": action.summary,
                "code": action.code,
                "object_id": action.object_id,
                "error": response.get("error_text") or response.get("error"),
            })
        else:
            results.append({
                "ok": True,
                "action": action.summary,
                "code": action.code,
                "object_id": action.object_id,
                "result": response.get("result") if isinstance(response, dict) else None,
            })
    return results


def save_log(ctx, plan, results) -> str:
    """Zhurnal pravok: chto bylo, chto stalo i chem zakonchilos'."""
    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = AUDITS_DIR / f"apply_{ctx.account}_{stamp}.json"

    payload = {
        "account": ctx.account,
        "applied_at": stamp,
        "actions": [a.to_dict() if isinstance(a, Action) else a for a in plan["actions"]],
        "results": results,
        "rollback_file": str(path),
        "how_to_rollback": f"rollback_apply(log_file='{path.name}', confirm=True)",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _reverse_payload(action: dict):
    """Stroim obratnyj zapros iz prepodnih znachenij."""
    service = action.get("service")
    object_id = action.get("object_id")
    before = action.get("before") or {}

    if service == "ads":
        if not before:
            return None, "нет прежних значений текста"
        return {"Ads": [{"Id": int(object_id), "TextAd": before}]}, None

    if service == "campaigns":
        daily = before.get("DailyBudget")
        if daily is None:
            return None, "снять дневной бюджет через API нельзя — снимите вручную"
        return {"Campaigns": [{"Id": int(object_id), "DailyBudget": daily}]}, None

    return None, f"откат для сервиса {service} не реализован"


def rollback(log_file: str, confirm: bool = False) -> dict:
    """Otkat pravok iz zhurnala. Bez confirm=True tol'ko pokazyvaem plan."""
    path = Path(log_file)
    if not path.is_absolute():
        path = AUDITS_DIR / log_file
    if not path.exists():
        return {"error": f"Журнал не найден: {path}"}

    data = json.loads(path.read_text(encoding="utf-8"))
    account = data.get("account")

    plan = []
    skipped = []
    for action in data.get("actions", []):
        payload, problem = _reverse_payload(action)
        if payload is None:
            skipped.append({"action": action.get("summary"), "reason": problem})
        else:
            plan.append({"summary": f"ОТКАТ: {action.get('summary')}",
                         "service": action.get("service"),
                         "method": action.get("method", "update"),
                         "payload": payload})

    result = {
        "account": account,
        "log": path.name,
        "dry_run": not confirm,
        "to_rollback": plan,
        "cannot_rollback": skipped,
    }
    if not confirm:
        result["next_step"] = "rollback_apply(log_file=..., confirm=True)"
        return result

    try:
        client = access.direct(account)
    except access.AccessError as e:
        return {**result, "error": str(e)}

    results = []
    for item in plan:
        response = client.post(item["service"], item["method"], item["payload"])
        ok = not (isinstance(response, dict) and response.get("error"))
        results.append({"ok": ok, "action": item["summary"],
                        "error": None if ok else (response.get("error_text") or response.get("error"))})
    result["results"] = results
    return result


def run_apply(account: str | None = None, date_range: str = "LAST_30_DAYS",
              confirm: bool = False, codes: list | None = None,
              only_active: bool = False) -> dict:
    """
    Polnyj tsikl: audit -> plan -> (pri confirm) ispolnenie -> zhurnal.

    Bez confirm=True ni odin zapros na izmenenie ne otpravlyaetsya.
    """
    ctx = build_context(account, date_range, only_active=only_active)
    if ctx.errors and ctx.errors[0]["label"] == "registry":
        return {"error": ctx.errors[0]["error"]}

    findings, failures = run_checks(ctx)
    plan = build_plan(ctx, findings, codes)

    result = {
        "account": ctx.account,
        "date_range": date_range,
        "dry_run": not confirm,
        "findings_total": len(findings),
        "actions_count": plan["actions_count"],
        "actions": [a.to_dict() for a in plan["actions"]],
        "fixers_available": plan["fixers_available"],
        "problems": plan["problems"],
        "not_automatic": plan["manual"],
        "check_failures": failures,
        "notes": ctx.notes,
    }

    if not confirm:
        result["warning"] = (
            "Это план (dry-run). Ничего не изменено. "
            "Для применения вызовите с confirm=True."
        )
        return result

    if not plan["actions"]:
        result["executed"] = []
        result["warning"] = "Нечего применять: автоматических правок не найдено."
        return result

    results = execute(ctx, plan["actions"])
    result["executed"] = results
    result["applied"] = sum(1 for r in results if r.get("ok"))
    result["failed"] = sum(1 for r in results if not r.get("ok"))
    result["log"] = save_log(ctx, plan, results)
    result["rollback_hint"] = f"rollback_apply(log_file='{Path(result['log']).name}', confirm=True)"
    return result

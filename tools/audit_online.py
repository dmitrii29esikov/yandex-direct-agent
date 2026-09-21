"""
Onlajn-audit po API: bez vygruzok Direct Commander.

Otlичае от tools/audit_files.py: tot chitaet XLSX, a etot — sam akkaunt.
"""

from mcp_instance import mcp

import accounts_store as store
from audit_engine import CHECKS, run_audit


@mcp.tool()
def audit_account(account: str | None = None,
                  date_range: str = "LAST_30_DAYS",
                  top: int = 10,
                  save_files: bool = True) -> dict:
    """
    Полный аудит аккаунта по API: Директ + Метрика + Tag Manager.

    Возвращает диагнозы (что исправлять и в каком порядке), находки по типам,
    список непроверенного и markdown-отчёт. Дополнительно сохраняет XLSX
    и чек-лист правок в data/audits/.

    Инструмент работает ТОЛЬКО НА ЧТЕНИЕ.

    :param account: имя аккаунта или любой знакомый ID (счётчик, контейнер, логин)
    :param date_range: период статистики (LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, ...)
    :param top: сколько диагнозов вернуть
    :param save_files: сохранять ли XLSX и remediation.md
    """
    return run_audit(account=account, date_range=date_range, top=top,
                     save_files=save_files)


@mcp.tool()
def audit_all_accounts(date_range: str = "LAST_30_DAYS", top: int = 5) -> dict:
    """
    Аудит всех аккаунтов реестра по очереди.

    Один «отвалившийся» клиент не ломает прогон остальных: ошибка попадает
    в результат этого аккаунта и работа продолжается.
    """
    results = []
    for name in store.account_ids():
        try:
            res = run_audit(account=name, date_range=date_range, top=top,
                            save_files=False)
        except Exception as e:
            res = {"account": name, "error": f"{type(e).__name__}: {e}"}
        results.append({
            "account": name,
            "error": res.get("error"),
            "findings_total": res.get("findings_total"),
            "errors": res.get("errors"),
            "top_diagnosis": res.get("top_diagnosis"),
            "not_checked": len(res.get("not_checked") or []),
        })
    ok = [r for r in results if not r.get("error")]
    return {
        "accounts": len(results),
        "ok": len(ok),
        "failed": len(results) - len(ok),
        "results": results,
    }


@mcp.tool()
def list_audit_checks() -> dict:
    """Список всех проверок аудита: код, контур, уровень, что ищет и как правится."""
    return {
        "count": len(CHECKS),
        "checks": [
            {"code": c.code, "contour": c.contour, "severity": c.severity,
             "fixable": c.fixable, "description": c.description}
            for c in CHECKS
        ],
    }

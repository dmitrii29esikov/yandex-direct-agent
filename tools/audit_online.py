"""
Onlajn-audit po API: bez vygruzok Direct Commander.

Otlичае от tools/audit_files.py: tot chitaet XLSX, a etot — sam akkaunt.
"""

from mcp_instance import mcp

import accounts_store as store
from audit_engine import CHECKS, FIXERS, rollback, run_apply, run_audit


@mcp.tool()
def audit_account(account: str | None = None,
                  date_range: str = "LAST_30_DAYS",
                  top: int = 10,
                  save_files: bool = True,
                  only_active: bool = False) -> dict:
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
    :param only_active: True — только запущенные кампании (State = ON),
                        архив и пауза не попадут в отчёт
    """
    return run_audit(account=account, date_range=date_range, top=top,
                     save_files=save_files, only_active=only_active)


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


# --------------------------------------------------------------------------
# Pravki (rezhim apply s dry-run)
# --------------------------------------------------------------------------

@mcp.tool()
def list_audit_fixers() -> dict:
    """Какие правки агент умеет применять сам, а какие только вручную."""
    return {
        "count": len(FIXERS),
        "fixers": [{"code": fx["code"], "description": fx["description"]}
                   for fx in FIXERS],
        "note": "Всё остальное правится вручную или в интерфейсе Tag Manager (ytm_ui).",
    }


@mcp.tool()
def apply_audit_fixes(account: str | None = None,
                      date_range: str = "LAST_30_DAYS",
                      confirm: bool = False,
                      codes: list[str] | None = None) -> dict:
    """
    Применение автоматических правок с обязательным dry-run.

    Без confirm=True НИЧЕГО не изменяется: инструмент только строит план
    (что именно и на что будет изменено). С confirm=True применяет изменения,
    пишет журнал в data/audits/apply_*.json и возвращает подсказку по откату.

    :param account: имя аккаунта или любой знакомый ID
    :param date_range: период для сбора статистики
    :param confirm: False — план (dry-run), True — применить
    :param codes: ограничить набор кодов (например ['DIRECT.TEXT_LENGTH'])
    """
    return run_apply(account=account, date_range=date_range,
                     confirm=confirm, codes=codes)


@mcp.tool()
def rollback_apply(log_file: str, confirm: bool = False) -> dict:
    """
    Откат применённых правок по журналу.

    Без confirm=True показывает, что именно будет откачено и что откатить
    нельзя (например, снятие дневного бюджета через API невозможно).

    :param log_file: имя файла журнала из data/audits/ или полный путь
    :param confirm: False — план отката, True — выполнить
    """
    return rollback(log_file=log_file, confirm=confirm)

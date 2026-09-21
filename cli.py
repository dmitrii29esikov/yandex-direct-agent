# cli.py
# Komandnaya stroka dlya Yandex Direct Agent.
# Zapuskaet MCP-instrumenty bez Chatbox i Cursor — udobno dlya bystroj proverki.
#
# Primery:
#   python cli.py accounts
#   python cli.py audit default
#   python cli.py --account printyard audit
#   python cli.py apply default            (dry-run, nichego ne menyaet)
#   python cli.py apply default --confirm  (primenit' pravki)
#   python cli.py stats --period LAST_7_DAYS

import sys
import json
import argparse
import asyncio

from server import mcp

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _run(tool_name: str, args: dict) -> None:
    """Vyzvat' MCP-instrument i napechatat' rezultat."""
    try:
        result = asyncio.run(mcp._tool_manager.call_tool(tool_name, args))
    except Exception as e:
        print(f"OSHIBKA: {e}")
        sys.exit(1)

    # FastMCP chasto oborachivaet otvet v content-bloki
    if hasattr(result, "content"):
        result = result.content

    # Vyrezayem skrytye kluchi vrode fullResultFileKey, esli oni est'
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            pass

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _with_account(payload: dict, args) -> dict:
    """Dobavlyaem account, esli on zadan i instrument ego prinimaet."""
    account = getattr(args, "account", None)
    if account:
        payload["account"] = account
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Yandex Direct Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Primery:\n"
            "  python cli.py accounts                      akkaunty reestra\n"
            "  python cli.py check default                 dostup po 3 konturam\n"
            "  python cli.py discover                      chto nam uzhe vydali\n"
            "  python cli.py audit default                 polnyj audit\n"
            "  python cli.py --account printyard audit     audit drugogo akkaunta\n"
            "  python cli.py apply default                 dry-run pravok\n"
            "  python cli.py apply default --confirm       primenit' pravki\n"
            "  python cli.py rollback apply_....json       otkat' pravki\n"
            "  python cli.py checks                        spisok proverok\n"
            "  python cli.py fixers                        chto pravitsya samo\n"
            "  python cli.py campaigns\n"
            "  python cli.py stats --period LAST_7_DAYS\n"
            "  python cli.py counters\n"
            "  python cli.py goals 105457694\n"
            "  python cli.py ytm-audit 1007795\n"
            "  python cli.py analyze --url https://printyard.spb.ru/\n"
        ),
    )
    parser.add_argument("--account", default=None,
                        help="Imya akkaunta ili lyuboj znakomyj ID "
                             "(schetchik, kontejner, login)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --- Reestr akkauntov ---
    sub.add_parser("accounts", help="Spisok akkauntov reestra")
    p = sub.add_parser("check", help="Proverka dostupov akkaunta")
    p.add_argument("name", nargs="?", default=None)
    p = sub.add_parser("describe", help="Pasport akkaunta")
    p.add_argument("name", nargs="?", default=None)
    p = sub.add_parser("discover", help="Najti vse, chto nam uzhe vydali")
    p.add_argument("--save", action="store_true",
                   help="Zapisat' najdennye privyazki v reestr")

    # --- Audit ---
    p = sub.add_parser("audit", help="Polnyj audit akkaunta")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--period", default="LAST_30_DAYS")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--no-files", action="store_true",
                   help="Ne sohranyat' XLSX i chek-list")
    p = sub.add_parser("audit-all", help="Audit vseh akkauntov reestra")
    p.add_argument("--period", default="LAST_30_DAYS")
    sub.add_parser("checks", help="Spisok proverok audita")
    sub.add_parser("fixers", help="Kakie pravki agent delaet sam")

    # --- Pravki ---
    p = sub.add_parser("apply", help="Pravki: bez --confirm tolko plan (dry-run)")
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--period", default="LAST_30_DAYS")
    p.add_argument("--confirm", action="store_true",
                   help="Primenit' izmeneniya. Bez etogo — tolko plan")
    p.add_argument("--codes", nargs="*", default=None,
                   help="Ogranichit' nabor kodov, napr. DIRECT.TEXT_LENGTH")
    p = sub.add_parser("rollback", help="Otkat' pravki po zhurnalu")
    p.add_argument("log_file")
    p.add_argument("--confirm", action="store_true")

    # --- Direct ---
    sub.add_parser("campaigns", help="Spisok kampanij")
    sub.add_parser("connection", help="Proverka podklyucheniya k Direct")

    # --- Metrika ---
    sub.add_parser("counters", help="Schetchiki Metriki")
    sub.add_parser("counter-names", help="ID i imena schetchikov")
    p = sub.add_parser("goals", help="Celi schetchika")
    p.add_argument("counter_id", type=int)
    p = sub.add_parser("goal-names", help="ID i imena celej schetchika")
    p.add_argument("counter_id", type=int)

    # --- Reports ---
    p = sub.add_parser("stats", help="Statistika kampanij za period")
    p.add_argument("--period", default="LAST_7_DAYS",
                   help="LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, ...")
    p.add_argument("--campaigns", nargs="*", type=int, default=None)
    p.add_argument("--goals", nargs="*", type=int, default=None)
    p.add_argument("--cpa-goal", type=int, default=None)
    p.add_argument("--fields", nargs="*", default=None)

    # --- Enrich ---
    p = sub.add_parser("names", help="Imena kampanij po ID")
    p.add_argument("--ids", nargs="*", type=int, default=None)

    # --- YTM ---
    p = sub.add_parser("ytm-tags", help="Tegi kontejnera YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-triggers", help="Triggery kontejnera YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-variables", help="Peremennye kontejnera YTM")
    p.add_argument("container_id", type=int)
    p.add_argument("--only-user", action="store_true")
    p = sub.add_parser("ytm-audit", help="Audit kontejnera YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-snapshot", help="Snyat' snapshot YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-changes", help="Diff YTM s poslednim snapshotom")
    p.add_argument("container_id", type=int)
    p.add_argument("--compare-with", default=None)
    sub.add_parser("ytm-list", help="Spisok snapshotov YTM")

    # --- Site ---
    p = sub.add_parser("analyze", help="Analiz sajta")
    p.add_argument("--url", required=True)
    p.add_argument("--no-js", action="store_true")
    p = sub.add_parser("fetch", help="Skachat' HTML")
    p.add_argument("--url", required=True)
    p.add_argument("--no-js", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    target = getattr(args, "name", None) or args.account

    if args.cmd == "accounts":
        _run("list_accounts", {})
    elif args.cmd == "check":
        _run("check_account", {"account": target})
    elif args.cmd == "describe":
        _run("describe_account", {"account": target})
    elif args.cmd == "discover":
        _run("discover_accounts", {"account": args.account, "save": args.save})

    elif args.cmd == "audit":
        _run("audit_account", {
            "account": target, "date_range": args.period,
            "top": args.top, "save_files": not args.no_files,
        })
    elif args.cmd == "audit-all":
        _run("audit_all_accounts", {"date_range": args.period, "top": 5})
    elif args.cmd == "checks":
        _run("list_audit_checks", {})
    elif args.cmd == "fixers":
        _run("list_audit_fixers", {})

    elif args.cmd == "apply":
        _run("apply_audit_fixes", {
            "account": target, "date_range": args.period,
            "confirm": args.confirm, "codes": args.codes,
        })
    elif args.cmd == "rollback":
        _run("rollback_apply", {"log_file": args.log_file, "confirm": args.confirm})

    # --- Direct ---
    elif args.cmd == "campaigns":
        _run("get_campaigns", _with_account({}, args))
    elif args.cmd == "connection":
        _run("check_api_connection", _with_account({}, args))
    # --- Metrika ---
    elif args.cmd == "counters":
        _run("get_metrica_counters", _with_account({}, args))
    elif args.cmd == "counter-names":
        _run("get_counter_names", _with_account({}, args))
    elif args.cmd == "goals":
        _run("get_metrica_goals", _with_account({"counter_id": args.counter_id}, args))
    elif args.cmd == "goal-names":
        _run("get_goal_names", _with_account({"counter_id": args.counter_id}, args))
    # --- Reports ---
    elif args.cmd == "stats":
        payload = {"date_range": args.period}
        if args.campaigns:
            payload["campaign_ids"] = args.campaigns
        if args.goals:
            payload["goal_ids"] = args.goals
        if args.cpa_goal:
            payload["cpa_goal_id"] = args.cpa_goal
        if args.fields:
            payload["fields"] = args.fields
        _run("get_campaign_stats", _with_account(payload, args))
    elif args.cmd == "names":
        payload = {}
        if args.ids:
            payload["campaign_ids"] = args.ids
        _run("get_campaign_names", _with_account(payload, args))
    # --- YTM ---
    elif args.cmd == "ytm-tags":
        _run("get_ytm_tags", _with_account({"container_id": args.container_id}, args))
    elif args.cmd == "ytm-triggers":
        _run("get_ytm_triggers", _with_account({"container_id": args.container_id}, args))
    elif args.cmd == "ytm-variables":
        _run("get_ytm_variables", _with_account({
            "container_id": args.container_id,
            "only_user": args.only_user,
        }, args))
    elif args.cmd == "ytm-audit":
        _run("audit_ytm_container", _with_account({"container_id": args.container_id}, args))
    elif args.cmd == "ytm-snapshot":
        _run("snapshot_ytm", _with_account({"container_id": args.container_id}, args))
    elif args.cmd == "ytm-changes":
        payload = {"container_id": args.container_id}
        if args.compare_with:
            payload["compare_with"] = args.compare_with
        _run("audit_ytm_changes", _with_account(payload, args))
    elif args.cmd == "ytm-list":
        _run("list_snapshots", {})
    # --- Site ---
    elif args.cmd == "analyze":
        _run("analyze_site", {"url": args.url, "use_js": not args.no_js})
    elif args.cmd == "fetch":
        _run("fetch_page", {"url": args.url, "use_js": not args.no_js})


if __name__ == "__main__":
    main()

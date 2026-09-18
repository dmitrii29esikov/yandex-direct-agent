# cli.py
# Komandnaya stroka dlya Yandex Direct Agent.
# Zapuskaet MCP-instrumenty bez Cursor.
# Primer: python cli.py campaigns
#         python cli.py stats --period LAST_7_DAYS
#         python cli.py analyze --url https://printyard.spb.ru/

import sys
import json
import argparse
import asyncio

from server import mcp


def _run(tool_name: str, args: dict) -> None:
    """Vyzvat' MCP-instrument i napechatat' rezul'tat."""
    try:
        result = asyncio.run(mcp._tool_manager.call_tool(tool_name, args))
    except Exception as e:
        print(f"OSHIBKA: {e}")
        sys.exit(1)

    # FastMCP chasto oborachivaet otvet v content-bloki
    if hasattr(result, "content"):
        result = result.content

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            pass

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Yandex Direct Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Primery:\n"
            "  python cli.py campaigns\n"
            "  python cli.py connection\n"
            "  python cli.py counters\n"
            "  python cli.py goals 105457694\n"
            "  python cli.py stats --period LAST_7_DAYS\n"
            "  python cli.py stats --period LAST_30_DAYS --goals 12345\n"
            "  python cli.py names\n"
            "  python cli.py counter-names\n"
            "  python cli.py ytm-tags 1007795\n"
            "  python cli.py ytm-triggers 1007795\n"
            "  python cli.py ytm-variables 1007795\n"
            "  python cli.py ytm-audit 1007795\n"
            "  python cli.py ytm-snapshot 1007795\n"
            "  python cli.py ytm-changes 1007795\n"
            "  python cli.py ytm-list\n"
            "  python cli.py analyze --url https://printyard.spb.ru/\n"
            "  python cli.py fetch --url https://printyard.spb.ru/\n"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

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
    p.add_argument("--campaigns", nargs="*", type=int, default=None,
                   help="Spisok ID kampanij")
    p.add_argument("--goals", nargs="*", type=int, default=None,
                   help="Spisok ID celej")
    p.add_argument("--cpa-goal", type=int, default=None,
                   help="ID celi dlya rascheta CPA")
    p.add_argument("--fields", nargs="*", default=None,
                   help="Polya otcheta")

    # --- Enrich ---
    p = sub.add_parser("names", help="Imя kampanij po ID")
    p.add_argument("--ids", nargs="*", type=int, default=None)

    # --- YTM ---
    p = sub.add_parser("ytm-tags", help="Tegi kontejnera YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-triggers", help="Triggery kontejnera YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-variables", help="Peremennye kontejnera YTM")
    p.add_argument("container_id", type=int)
    p.add_argument("--only-user", action="store_true",
                   help="Tol'ko pol'zovatel'skie peremennye")
    p = sub.add_parser("ytm-audit", help="Audit kontejnera YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-snapshot", help="Snyat' snímok YTM")
    p.add_argument("container_id", type=int)
    p = sub.add_parser("ytm-changes", help="Diff YTM s poslednim snímkom")
    p.add_argument("container_id", type=int)
    p.add_argument("--compare-with", default=None)
    sub.add_parser("ytm-list", help="Spisok snímkov YTM")

    # --- Site ---
    p = sub.add_parser("analyze", help="Analiz sajta")
    p.add_argument("--url", required=True)
    p.add_argument("--no-js", action="store_true",
                   help="Bez Playwright (bystro, bez JS)")
    p = sub.add_parser("fetch", help="Skachat' HTML")
    p.add_argument("--url", required=True)
    p.add_argument("--no-js", action="store_true")

    args = parser.parse_args()

    # --- Dispatch ---
    if args.cmd == "campaigns":
        _run("get_campaigns", {})
    elif args.cmd == "connection":
        _run("check_api_connection", {})
    elif args.cmd == "counters":
        _run("get_metrica_counters", {})
    elif args.cmd == "counter-names":
        _run("get_counter_names", {})
    elif args.cmd == "goals":
        _run("get_metrica_goals", {"counter_id": args.counter_id})
    elif args.cmd == "goal-names":
        _run("get_goal_names", {"counter_id": args.counter_id})
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
        _run("get_campaign_stats", payload)
    elif args.cmd == "names":
        payload = {}
        if args.ids:
            payload["campaign_ids"] = args.ids
        _run("get_campaign_names", payload)
    elif args.cmd == "ytm-tags":
        _run("get_ytm_tags", {"container_id": args.container_id})
    elif args.cmd == "ytm-triggers":
        _run("get_ytm_triggers", {"container_id": args.container_id})
    elif args.cmd == "ytm-variables":
        _run("get_ytm_variables", {
            "container_id": args.container_id,
            "only_user": args.only_user,
        })
    elif args.cmd == "ytm-audit":
        _run("audit_ytm_container", {"container_id": args.container_id})
    elif args.cmd == "ytm-snapshot":
        _run("snapshot_ytm", {"container_id": args.container_id})
    elif args.cmd == "ytm-changes":
        payload = {"container_id": args.container_id}
        if args.compare_with:
            payload["compare_with"] = args.compare_with
        _run("audit_ytm_changes", payload)
    elif args.cmd == "ytm-list":
        _run("list_snapshots", {})
    elif args.cmd == "analyze":
        _run("analyze_site", {"url": args.url, "use_js": not args.no_js})
    elif args.cmd == "fetch":
        _run("fetch_page", {"url": args.url, "use_js": not args.no_js})


if __name__ == "__main__":
    main()
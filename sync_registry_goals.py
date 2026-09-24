# -*- coding: utf-8 -*-
"""
Sinhronizaciya reestra s API: direct.priority_goals = fakticheskie tseli kampanij.

Reestr — rezervnyj istochnik na sluchaj, kogda strategiyu prochitat' ne udalos'.
On zastarevaet: tseli menяyut v interfejse. Skripti privodit ego k API.

Chto delaem:
 1. chitaem kampanii i ih tseli iz API (PriorityGoals + tseli strategij);
 2. sohranyaem prezhnee znachenie v data/registry_backups/ (otkat vozmozhen);
 3. zapisyvaem novoe znachenie v data/accounts.json;
 4. pechataem tablicu «bylo → stalo» po kampaniyam.

Zapusk: python sync_registry_goals.py anton
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import access
import accounts_store as store

BACKUP_DIR = Path("data") / "registry_backups"


def main(account_name: str) -> int:
    name = store.resolve_or_default(account_name)
    record = store.get(name)
    direct = record.setdefault("direct", {})
    before = {str(k): sorted(int(g) for g in v)
              for k, v in (direct.get("priority_goals") or {}).items()}

    data = access.campaign_strategies(name)
    if data.get("error"):
        print(json.dumps({"error": data["error"]}, ensure_ascii=False))
        return 1

    campaigns = data.get("campaigns") or {}
    after = {}
    for cid, info in sorted(campaigns.items()):
        # Arhivnye kampanii deneg ne tratyat: ih tseli v rezervnom istochnike
        # tol'ko pomehayut — pri otkaze API ih prinyali by za rabochie.
        if info.get("state") == "ARCHIVED":
            continue
        goals = sorted({int(g) for g in (info.get("goals_all") or [])})
        if goals:
            after[str(cid)] = goals

    names = {str(cid): info.get("name") for cid, info in campaigns.items()}
    states = {str(cid): info.get("state") for cid, info in campaigns.items()}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = BACKUP_DIR / f"priority_goals_{name}_{stamp}.json"
    backup.write_text(json.dumps(before, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    direct["priority_goals"] = after
    store.upsert_account(name, record)

    goal_names = access.goal_names(name, [
        c for info in campaigns.values() for c in (info.get("counter_ids") or [])
    ])

    print(f"Аккаунт: {name} · кампаний прочитано: {len(campaigns)}")
    print(f"Бэкап прежнего реестра: {backup}")
    print()
    print(f"{'кампания':>12}  состояние  было → стало")
    for cid in sorted(set(before) | set(after)):
        old, new = before.get(cid) or [], after.get(cid) or []
        mark = "" if old == new else "  ← изменено"
        if not mark and cid not in before and cid not in after:
            continue
        label = (names.get(cid) or "")[:38]
        old_text = "; ".join(access.label_goals(goal_names, old)) or "—"
        new_text = "; ".join(access.label_goals(goal_names, new)) or "—"
        if old == new:
            print(f"{cid:>12}  {states.get(cid, '—'):<10}  {new_text} (без изменений)")
        else:
            print(f"{cid:>12}  {states.get(cid, '—'):<10}  {old_text}")
            print(f"{'':>12}  {'':<10}  → {new_text}{mark}   [{label}]")

    resolved = access.priority_goals_resolved(name)
    print()
    print("Расхождений реестра с API:", len(resolved.get("mismatch") or {}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "anton"))

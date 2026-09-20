from mcp_instance import mcp, api_client
# Audit izmenenij konfiguratsii YTM.
# Snimaem "snapshot" (srez sostoyaniya) i sravnivaem ego s predyduschim.
# Kommentarii translitom, chtoby ne bylo krakozyabr v Windows-1251.

import os
import json
import glob
from datetime import datetime

from server import mcp
from tools import tag_manager


SNAPSHOTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "snapshots",
)


def _ensure_dir() -> str:
    """Sozdaem papku dlya snímkov, esli eyo net."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    return SNAPSHOTS_DIR


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _snapshot_path(container_id: int, ts: str) -> str:
    return os.path.join(_ensure_dir(), f"ytm_{container_id}_{ts}.json")


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _latest_snapshot(container_id: int):
    """Poslednij po vremeni snapshot dlya kontejnera (ili None)."""
    _ensure_dir()
    pattern = os.path.join(SNAPSHOTS_DIR, f"ytm_{container_id}_*.json")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def _extract_ytm_state(container_id: int) -> dict:
    """Snimaem tekushchee sostoyanie kontejnera YTM."""
    tags = tag_manager.get_ytm_tags(container_id)
    triggers = tag_manager.get_ytm_triggers(container_id)
    variables = tag_manager.get_ytm_variables(container_id, only_user=True)

    if "error" in tags and "tags" not in tags:
        return {"error": tags["error"]}
    if "error" in triggers and "triggers" not in triggers:
        return {"error": triggers["error"]}
    if "error" in variables and "variables" not in variables:
        return {"error": variables["error"]}

    return {
        "container_id": container_id,
        "snapshot_time": _now_str(),
        "tags": tags.get("tags", []),
        "triggers": triggers.get("triggers", []),
        "variables": variables.get("variables", []),
    }


def _by_key(items: list, key: str) -> dict:
    """Prevraschaem spisok v dict po klyuchu (tag_id/trigger_id/variable_id)."""
    out = {}
    for it in items:
        k = it.get(key)
        if k is not None:
            out[str(k)] = it
    return out


def _diff_section(name: str, old_items: list, new_items: list, key: str) -> dict:
    """Sravnivaem dve kollektsii po klyuchu. Vozvrashchaem added/removed/changed."""
    old_map = _by_key(old_items, key)
    new_map = _by_key(new_items, key)

    added = []
    removed = []
    changed = []

    for k, new_it in new_map.items():
        if k not in old_map:
            added.append({"key": k, "name": new_it.get("name")})
        else:
            old_it = old_map[k]
            diffs = {}
            for field in ("name", "type", "status", "links_number", "triggers"):
                if old_it.get(field) != new_it.get(field):
                    diffs[field] = {
                        "old": old_it.get(field),
                        "new": new_it.get(field),
                    }
            if diffs:
                changed.append({
                    "key": k,
                    "name": new_it.get("name"),
                    "fields": diffs,
                })

    for k, old_it in old_map.items():
        if k not in new_map:
            removed.append({"key": k, "name": old_it.get("name")})

    return {
        "section": name,
        "added": added,
        "removed": removed,
        "changed": changed,
    }


@mcp.tool()
def snapshot_ytm(container_id: int) -> dict:
    """
    Snimaem tekushchee sostoyanie YTM-kontejnera i sohranyaem v fajl.
    Fajl: data/snapshots/ytm_{container_id}_{YYYY-MM-DD_HH-MM-SS}.json

    :param container_id: ID kontejnera YTM (naprimer 1007795)
    """
    state = _extract_ytm_state(container_id)
    if "error" in state:
        return state

    ts = _now_str()
    path = _snapshot_path(container_id, ts)
    _save_json(path, state)

    return {
        "container_id": container_id,
        "saved_to": path,
        "summary": {
            "tags": len(state["tags"]),
            "triggers": len(state["triggers"]),
            "variables_user": len(state["variables"]),
        },
    }


@mcp.tool()
def list_snapshots(container_id: int | None = None) -> dict:
    """
    Spisok sohranennyh snímkov. Esli container_id ne zadan — vse.

    :param container_id: ID kontejnera (optsional'no)
    """
    _ensure_dir()
    if container_id is None:
        pattern = os.path.join(SNAPSHOTS_DIR, "ytm_*.json")
    else:
        pattern = os.path.join(SNAPSHOTS_DIR, f"ytm_{container_id}_*.json")

    files = sorted(glob.glob(pattern))
    out = []
    for f in files:
        try:
            st = os.stat(f)
            out.append({
                "path": f,
                "name": os.path.basename(f),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"),
            })
        except Exception:
            continue

    return {"count": len(out), "snapshots": out}


@mcp.tool()
def audit_ytm_changes(container_id: int, compare_with: str | None = None) -> dict:
    """
    Sravnivaem tekushchee sostoyanie YTM s poslednim snímkom.
    Esli compare_with zadan — s konkretnym fajlom.

    :param container_id: ID kontejnera YTM
    :param compare_with: imya fajla-snimka dlya sravneniya (optsional'no)
    """
    if compare_with:
        if os.path.isabs(compare_with):
            old_path = compare_with
        else:
            old_path = os.path.join(SNAPSHOTS_DIR, compare_with)
        if not os.path.exists(old_path):
            return {"error": f"Snímok ne najden: {old_path}"}
    else:
        old_path = _latest_snapshot(container_id)
        if not old_path:
            return {
                "error": "Net ni odnogo snímka. Snachala vypolni "
                         "snapshot_ytm(container_id=...).",
                "hint": f"snapshot_ytm({container_id})",
            }

    new_state = _extract_ytm_state(container_id)
    if "error" in new_state:
        return new_state

    old_state = _load_json(old_path)

    tags_diff = _diff_section("tags",
                              old_state.get("tags", []),
                              new_state.get("tags", []),
                              "tag_id")
    triggers_diff = _diff_section("triggers",
                                  old_state.get("triggers", []),
                                  new_state.get("triggers", []),
                                  "trigger_id")
    variables_diff = _diff_section("variables",
                                   old_state.get("variables", []),
                                   new_state.get("variables", []),
                                   "variable_id")

    total_changes = (
        len(tags_diff["added"]) + len(tags_diff["removed"]) + len(tags_diff["changed"])
        + len(triggers_diff["added"]) + len(triggers_diff["removed"]) + len(triggers_diff["changed"])
        + len(variables_diff["added"]) + len(variables_diff["removed"]) + len(variables_diff["changed"])
    )

    return {
        "container_id": container_id,
        "compared_with": os.path.basename(old_path),
        "old_snapshot_time": old_state.get("snapshot_time"),
        "new_snapshot_time": new_state.get("snapshot_time"),
        "total_changes": total_changes,
        "has_changes": total_changes > 0,
        "diff": {
            "tags": tags_diff,
            "triggers": triggers_diff,
            "variables": variables_diff,
        },
    }
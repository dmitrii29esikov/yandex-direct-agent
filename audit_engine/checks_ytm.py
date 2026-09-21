"""
Proverki Yandex Tag Manager.

Ogranichenie platformy: API YTM rabotaet TOLKO na chtenie. Poetomu vse
ispravleniya zdes' pomechayutsya kak 'ytm_ui' — ih nuzhno delat' v interfejse.
"""

from .base import register

MAX_EXAMPLES = 20


def _container_meta(container_id):
    return {"object_type": "container", "object_id": str(container_id)}


@register("YTM.EMPTY_CONTAINER", "ytm", "warning", description="Пустой контейнер")
def check_empty_container(ctx):
    out = []
    for cid, buckets in (ctx.data.get("ytm") or {}).items():
        tags = buckets.get("tags")
        if tags is not None and not tags:
            out.append(dict(
                title="В контейнере нет тегов",
                detail="Контейнер создан, но теги не добавлены — разметка не работает",
                evidence={"теги": 0},
                fix="Добавить теги в интерфейсе Tag Manager",
                fixable="ytm_ui",
                **_container_meta(cid),
            ))
    return out


@register("YTM.TAGS_WITHOUT_TRIGGERS", "ytm", "warning", fixable="ytm_ui",
          description="Теги без триггеров")
def check_tags_without_triggers(ctx):
    out = []
    for cid, buckets in (ctx.data.get("ytm") or {}).items():
        tags = buckets.get("tags")
        if not tags:
            continue
        broken = [{"name": t.get("name"), "tag_id": t.get("tag_id")}
                  for t in tags if not t.get("triggers")]
        if broken:
            out.append(dict(
                title="Теги без триггеров",
                detail=f"{len(broken)} тегов не сработают: у них не задано условие показа",
                evidence={"всего": len(broken), "примеры": broken[:MAX_EXAMPLES]},
                fix="Привязать триггер к каждому тегу",
                **_container_meta(cid),
            ))
    return out


@register("YTM.TRIGGERS_UNUSED", "ytm", "warning", fixable="ytm_ui",
          description="Триггеры, на которые никто не ссылается")
def check_triggers_unused(ctx):
    out = []
    for cid, buckets in (ctx.data.get("ytm") or {}).items():
        triggers = buckets.get("triggers")
        if not triggers:
            continue
        unused = [{"name": t.get("name"), "trigger_id": t.get("trigger_id")}
                  for t in triggers if not t.get("links_number")]
        if unused:
            out.append(dict(
                title="Неиспользуемые триггеры",
                detail=f"{len(unused)} триггеров не привязаны ни к одному тегу",
                evidence={"всего": len(unused), "примеры": unused[:MAX_EXAMPLES]},
                fix="Удалить лишние триггеры или привязать к тегам",
                **_container_meta(cid),
            ))
    return out


@register("YTM.VARIABLES_UNUSED", "ytm", "info", fixable="ytm_ui",
          description="Неиспользуемые пользовательские переменные")
def check_variables_unused(ctx):
    out = []
    for cid, buckets in (ctx.data.get("ytm") or {}).items():
        variables = buckets.get("variables")
        if not variables:
            continue
        unused = []
        for v in variables:
            vid = str(v.get("variable_id") or "")
            if vid.isdigit() and not v.get("links_number"):   # tol'ko pol'zovatel'skie
                unused.append({"name": v.get("name"), "variable_id": vid})
        if unused:
            out.append(dict(
                title="Неиспользуемые переменные",
                detail=f"{len(unused)} пользовательских переменных не используются в тегах",
                evidence={"всего": len(unused), "примеры": unused[:MAX_EXAMPLES]},
                fix="Удалить или задействовать переменные",
                **_container_meta(cid),
            ))
    return out


@register("YTM.DUPLICATE_NAMES", "ytm", "info", fixable="ytm_ui",
          description="Дубликаты имён тегов, триггеров или переменных")
def check_duplicates(ctx):
    out = []
    for cid, buckets in (ctx.data.get("ytm") or {}).items():
        for section, id_key in (("tags", "tag_id"), ("triggers", "trigger_id"),
                               ("variables", "variable_id")):
            items = buckets.get(section) or []
            seen = {}
            for item in items:
                seen.setdefault(item.get("name"), []).append(item.get(id_key))
            dupes = [{"name": n, "ids": ids} for n, ids in seen.items() if len(ids) > 1]
            if dupes:
                out.append(dict(
                    title=f"Дубликаты имён в разделе {section}",
                    detail=f"{len(dupes)} совпадающих имён — легко перепутать при правках",
                    evidence={"примеры": dupes[:MAX_EXAMPLES]},
                    fix="Переименовать объекты так, чтобы имена были уникальны",
                    **_container_meta(cid),
                ))
    return out

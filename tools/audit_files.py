"""
Аудит выгрузок Яндекс.Директ Коммандера (XLSX/XLS).
"""
import re
import json
import argparse
from pathlib import Path
from collections import Counter
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
from mcp_instance import mcp

LIMITS = {
    "Заголовок 1": 56, "Заголовок 2": 56, "Заголовок 3": 56,
    "Заголовок 4": 56, "Заголовок 5": 56, "Заголовок 6": 56, "Заголовок 7": 56,
    "Текст": 81, "Текст 1": 81, "Текст 2": 81, "Текст 3": 81,
    "Отображаемая ссылка": 20,
}
WORD_LIMITS = {
    "Заголовок 1": 22, "Заголовок 2": 22, "Заголовок 3": 22,
    "Заголовок 4": 22, "Заголовок 5": 22, "Заголовок 6": 22, "Заголовок 7": 22,
    "Текст": 23, "Текст 1": 23, "Текст 2": 23, "Текст 3": 23,
}
PUNCT_LIMIT = 15
QUICK_TITLE_MAX = 30
QUICK_DESC_MAX, QUICK_WORD_MAX, QUICK_MAX_ITEMS = 60, 23, 8
UTO_MAX_LEN, LABEL_MAX_LEN = 25, 25
PHRASE_MAX, PHRASE_WORD_MAX = 4096, 35
MINUS_GROUP_MAX, MINUS_CAMPAIGN_MAX = 4096, 20000
MINUS_WORDS_MAX = 7

SECTION_MARKERS = {"Комбинаторика", "Длина", "Продвижение приложений: настройки на группу"}

SHEET_DESCRIPTIONS = [
    ("Оглавление", "Описание всех разделов отчёта."),
    ("Сводка", "Ключевые цифры."),
    ("1_Аудит_проблемы", "Проверки по правилам Директ Коммандера."),
    ("2_Паспорт_кампаний", "Паспорт кампании."),
    ("3_Объявления", "Все объявления."),
    ("4_Изображения", "Анализ изображений."),
    ("5_Фразы", "Ключевые и минус-фразы по кампании и группам."),
    ("6_Минус_фразы", "Минус-фразы кампании и групп."),
    ("Файлы_и_листы", "Прочитанные листы."),
    ("Метаданные", "Метаданные кампаний."),
    ("Справочник_Регионы", "Регионы."),
    ("Справочник_Поля", "Словарь полей."),
]


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s) if s is not None else "").strip()


def _base_field_name(name: str) -> str:
    return name[5:] if name.startswith("Комб.") else name


def _parse_minus_phrases(text: str) -> List[str]:
    if not text:
        return []
    parts = text.replace(" -", "\n-").split("\n")
    return [p.strip() for p in parts if p.strip()]


def _phrase_type(phrase: str) -> str:
    if phrase.startswith("audience:"):
        return "Аудитория"
    if phrase.startswith("---"):
        return "Автотаргетинг"
    return "Ключевая"


def _phrase_operators(phrase: str) -> str:
    ops = []
    if phrase.startswith('"') and phrase.endswith('"') and len(phrase) > 1:
        ops.append('"..."')
    if "[" in phrase and "]" in phrase:
        ops.append("[]")
    if "+" in phrase:
        ops.append("+")
    if "!" in phrase:
        ops.append("!")
    return ", ".join(ops)


def _minus_word_count(phrase: str) -> int:
    clean = phrase.lstrip("-").replace("!", "").replace('"', "").replace("[", "").replace("]", "").replace("+", "")
    return len([w for w in clean.split() if w])


def _iter_sheets(path: Path):
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        xls = pd.ExcelFile(path)
        for name in xls.sheet_names:
            yield name, pd.read_excel(xls, sheet_name=name, header=None, dtype=str)
        return
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        for sep in ("\t", ",", ";"):
            try:
                df = pd.read_csv(path, header=None, dtype=str, encoding=enc,
                                 sep=sep, engine="python", quoting=3, on_bad_lines="skip")
                if df.shape[1] > 1:
                    yield path.name, df
                    return
            except Exception:
                continue
    raise ValueError(f"Не удалось прочитать {path}")


def _find_header(df_raw: pd.DataFrame) -> Tuple[Optional[int], Optional[int]]:
    for i in range(min(80, len(df_raw))):
        row = [_clean(c).lower() for c in df_raw.iloc[i].fillna("").tolist()]
        if any("id группы" in c for c in row) and any("id объявления" in c for c in row):
            sub_idx = i + 1 if i + 1 < len(df_raw) else None
            if sub_idx is not None:
                sub = [_clean(c).lower() for c in df_raw.iloc[sub_idx].fillna("").tolist()]
                if any(("заголовок 1" in c) or ("текст 1" in c) for c in sub):
                    return i, sub_idx
            return i, None
    return None, None


def _build_columns(df_raw: pd.DataFrame, main_idx: int, sub_idx: Optional[int]) -> List[str]:
    main = [_clean(c) for c in df_raw.iloc[main_idx].fillna("").tolist()]
    if sub_idx is None:
        return main
    sub = [_clean(c) for c in df_raw.iloc[sub_idx].fillna("").tolist()]
    cols: List[str] = []
    inside_length_block = False
    for m, s in zip(main, sub):
        if m == "Длина":
            inside_length_block = True
            cols.append("")
            continue
        if inside_length_block and not m:
            cols.append("")
            continue
        if m:
            inside_length_block = False
        if m in SECTION_MARKERS:
            name = s or m
            cols.append(f"Комб.{name}" if name else "")
            continue
        if not m and s:
            cols.append(s)
            continue
        if m and not s:
            cols.append(m)
            continue
        if m.lower() == s.lower():
            cols.append(m)
            continue
        cols.append(s)
    return cols


def _unique_names(names: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out = []
    for n in names:
        if not n:
            out.append(n)
            continue
        if n in seen:
            seen[n] += 1
            out.append(f"{n}__{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def _read_metadata(df_raw: pd.DataFrame) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for i in range(min(20, len(df_raw))):
        row = [_clean(c) for c in df_raw.iloc[i].fillna("").tolist()]
        for j, cell in enumerate(row):
            if not cell or not cell.endswith(":"):
                continue
            key = cell.rstrip(":").strip()
            if not key:
                continue
            for k in range(j + 1, len(row)):
                val = row[k].strip()
                if val and not val.endswith(":"):
                    meta.setdefault(key, val)
                    break
    return meta


def _parse_regions(df_raw: pd.DataFrame) -> List[str]:
    names: List[str] = []
    seen = set()
    for i in range(len(df_raw)):
        for cell in df_raw.iloc[i].fillna("").tolist():
            name = _clean(cell)
            if not name or name.lower() == "регионы":
                continue
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _max_word(s: str) -> int:
    words = re.split(r"[\s,.;:!?\"'()\-]+", s)
    return max((len(w) for w in words if w), default=0)


def _count_punct(s: str) -> int:
    return sum(1 for ch in s if ch in ",.;:!?\"'— -()")


def _check_row(row, cmap, meta, file_name, sheet, row_no, issues):
    def g(key):
        i = cmap.get(key)
        if i is None or i >= len(row):
            return ""
        return _clean(row[i])

    gid, gname = g("ID группы"), g("Название группы")
    aid, pid = g("ID объявления"), g("ID фразы")
    ctx = {"Файл": file_name, "Лист": sheet, "Строка": row_no,
           "ID группы": gid, "ID объявления": aid, "ID фразы": pid,
           "Тип кампании": meta.get("Тип кампании", "")}

    def add(level, typ, msg):
        issues.append({**ctx, "Уровень": level, "Тип проблемы": typ, "Сообщение": msg})

    for key in ("Доп. объявление группы", "Название группы"):
        if key in cmap and not g(key):
            add("Ошибка", "Пустое обязательное поле", f"Пустое обязательное поле: {key}")

    dop = g("Доп. объявление группы")
    if dop and dop not in ("-", "+"):
        add("Ошибка", "Некорректное доп. объявление", f"Доп. объявление группы = '{dop}'")

    num = g("Номер группы")
    if num and not num.isdigit():
        add("Ошибка", "Номер группы не цифры", f"Номер группы не цифры: {num}")

    phrase = g("Фраза (с минус-словами)")
    if phrase:
        if _phrase_type(phrase) == "Ключевая":
            if len(phrase) > PHRASE_MAX:
                add("Ошибка", "Фраза слишком длинная", f"Фраза > {PHRASE_MAX}: {len(phrase)}")
            mw = _max_word(phrase)
            if mw > PHRASE_WORD_MAX:
                add("Предупреждение", "Слово во фразе длинное", f"Слово > {PHRASE_WORD_MAX}: {mw}")

    for col_name in cmap.keys():
        base = _base_field_name(col_name)
        val = g(col_name)
        if not val:
            continue
        limit = LIMITS.get(base)
        if limit and len(val) > limit:
            add("Ошибка", "Превышение длины", f"{col_name}: длина {len(val)} > {limit}")
        wl = WORD_LIMITS.get(base)
        if wl:
            mw = _max_word(val)
            if mw > wl:
                add("Предупреждение", "Слишком длинное слово", f"{col_name}: слово {mw} > {wl}")
        if base.startswith("Текст"):
            pc = _count_punct(val)
            if pc > PUNCT_LIMIT:
                add("Предупреждение", "Много знаков препинания", f"{col_name}: знаков {pc} > {PUNCT_LIMIT}")

    url = g("Ссылка")
    if url and not re.match(r"^https?://", url, re.I):
        add("Ошибка", "Некорректная ссылка", f"Ссылка без http(s): {url}")

    dur = g("Отображаемая ссылка")
    if dur:
        if len(dur) > LIMITS["Отображаемая ссылка"]:
            add("Ошибка", "Отображаемая ссылка длинная", f"Отображаемая ссылка > {LIMITS['Отображаемая ссылка']}")
        if "://" in dur or "." in dur:
            add("Предупреждение", "Домен в отображаемой ссылке", f"Домен/протокол: {dur}")
        if not re.match(r"^[A-Za-zА-Яа-я0-9\-№/%#]+$", dur):
            add("Предупреждение", "Недопустимые символы", f"Недопустимые символы: {dur}")

    qt, qd, qu = g("Заголовки быстрых ссылок"), g("Описания быстрых ссылок"), g("Адреса быстрых ссылок")
    if qt or qd or qu:
        titles = [x.strip() for x in qt.split("||") if x.strip()]
        descs = [x.strip() for x in qd.split("||") if x.strip()]
        urls = [x.strip() for x in qu.split("||") if x.strip()]
        if not (len(titles) == len(descs) == len(urls)):
            add("Ошибка", "Быстрые ссылки: несовпадение", f"заголовков {len(titles)}, описаний {len(descs)}, адресов {len(urls)}")
        if len(titles) > QUICK_MAX_ITEMS:
            add("Предупреждение", "Много быстрых ссылок", f"Быстрых ссылок > {QUICK_MAX_ITEMS}: {len(titles)}")
        for t in titles:
            if len(t) > QUICK_TITLE_MAX:
                add("Предупреждение", "Заголовок быстрой ссылки длинный", f">{QUICK_TITLE_MAX}: {len(t)}")
            if _max_word(t) > QUICK_WORD_MAX:
                add("Предупреждение", "Слово в быстрой ссылке длинное", f">{QUICK_WORD_MAX}: {t}")
            if re.search(r"[!?\[\]]", t):
                add("Ошибка", "Запрещённые символы", f"Запрещённые символы: {t}")
        for d in descs:
            if len(d) > QUICK_DESC_MAX:
                add("Предупреждение", "Описание длинное", f">{QUICK_DESC_MAX}: {len(d)}")
            if _max_word(d) > QUICK_WORD_MAX:
                add("Предупреждение", "Слово в описании длинное", f">{QUICK_WORD_MAX}: {d}")
            if re.search(r"[!?\[\]]", d):
                add("Ошибка", "Запрещённые символы", f"Запрещённые символы: {d}")
        for u in urls:
            if not re.match(r"^https?://", u, re.I):
                add("Ошибка", "Некорректный адрес быстрой ссылки", f"Не URL: {u}")

    ut = g("Уточнения")
    if ut:
        items = [x.strip() for x in ut.split("||") if x.strip()]
        if len(items) > 25:
            add("Предупреждение", "Много уточнений", f"> 25: {len(items)}")
        for x in items:
            if len(x) > UTO_MAX_LEN:
                add("Предупреждение", "Уточнение длинное", f">{UTO_MAX_LEN}: {x}")
        if len(set(items)) != len(items):
            add("Предупреждение", "Дубли уточнений", "Есть дубли")

    marks = g("Метки")
    if marks:
        for x in [x.strip() for x in marks.split(",") if x.strip()]:
            if len(x) > LABEL_MAX_LEN:
                add("Предупреждение", "Метка длинная", f">{LABEL_MAX_LEN}: {x}")

    mg = g("Минус-фразы на группу")
    if mg:
        if len(mg) > MINUS_GROUP_MAX:
            add("Ошибка", "Минус-фразы группы длинные", f">{MINUS_GROUP_MAX}: {len(mg)}")
        phrases = _parse_minus_phrases(mg)
        for p in phrases:
            wc = _minus_word_count(p)
            if wc > MINUS_WORDS_MAX:
                add("Ошибка", "Минус-фраза: много слов", f"'{p}' — {wc} слов (макс {MINUS_WORDS_MAX})")

def _audit_texts(df_raw, meta, file_name, sheet_name):
    issues = []
    main_idx, sub_idx = _find_header(df_raw)
    if main_idx is None:
        issues.append({"Файл": file_name, "Лист": sheet_name, "Уровень": "Ошибка",
                       "Тип проблемы": "Нет шапки",
                       "Сообщение": "Не найдена шапка с 'ID группы'/'ID объявления'"})

        return issues, [], [], []
    cols = _unique_names(_build_columns(df_raw, main_idx, sub_idx))
    cmap = {n: i for i, n in enumerate(cols) if n}
    data_start = (sub_idx + 1) if sub_idx is not None else (main_idx + 1)

    ads, images, phrases = [], [], []
    for i in range(data_start, len(df_raw)):
        row = df_raw.iloc[i].fillna("").tolist()
        if all(_clean(c) == "" for c in row):
            continue
        _check_row(row, cmap, meta, file_name, sheet_name, i + 1, issues)

        ad = {"Файл": file_name, "Лист": sheet_name, "Строка": i + 1,
              "Тип кампании": meta.get("Тип кампании", "")}
        for name, idx in cmap.items():
            ad[name] = _clean(row[idx]) if idx < len(row) else ""
        ads.append(ad)

        for col_name, val in ad.items():
            if (col_name.startswith("Изображение")
                    or col_name.startswith("Комб.Изображение")
                    or col_name == "Креатив") and val:
                images.append({
                    "Файл": file_name, "Лист": sheet_name, "Строка": i + 1,
                    "ID объявления": ad.get("ID объявления", ""),
                    "ID группы": ad.get("ID группы", ""),
                    "Колонка": col_name, "URL изображения": val,
                })

    gi = cmap.get("ID группы")
    gni = cmap.get("Название группы")
    gnumi = cmap.get("Номер группы")
    pi = cmap.get("Фраза (с минус-словами)")
    pidi = cmap.get("ID фразы")
    si = cmap.get("Статус фразы")
    sti = cmap.get("Ставка")
    ri = cmap.get("Регион")

    for i in range(data_start, len(df_raw)):
        row = df_raw.iloc[i].fillna("").tolist()
        phrase = _clean(row[pi]) if pi is not None and pi < len(row) else ""
        if not phrase:
            continue
        phrases.append({
            "Файл": file_name,
            "Тип кампании": meta.get("Тип кампании", ""),
            "ID группы": _clean(row[gi]) if gi is not None and gi < len(row) else "",
            "Название группы": _clean(row[gni]) if gni is not None and gni < len(row) else "",
            "Номер группы": _clean(row[gnumi]) if gnumi is not None and gnumi < len(row) else "",
            "ID фразы": _clean(row[pidi]) if pidi is not None and pidi < len(row) else "",
            "Фраза": phrase,
            "Тип фразы": _phrase_type(phrase),
            "Операторы": _phrase_operators(phrase),
            "Статус фразы": _clean(row[si]) if si is not None and si < len(row) else "",
            "Ставка": _clean(row[sti]) if sti is not None and sti < len(row) else "",
            "Регион": _clean(row[ri]) if ri is not None and ri < len(row) else "",
            "Длина": len(phrase),
        })

    phrase_cnt = Counter()
    if gi is not None and pi is not None:
        for i in range(data_start, len(df_raw)):
            row = df_raw.iloc[i].fillna("").tolist()
            if gi < len(row) and pi < len(row):
                gid, phr = _clean(row[gi]), _clean(row[pi])
                if gid and phr:
                    phrase_cnt[(gid, phr)] += 1
    for (gid, phr), cnt in phrase_cnt.items():
        if cnt > 1:
            issues.append({"Файл": file_name, "Лист": sheet_name, "Уровень": "Предупреждение",
                           "Тип проблемы": "Дубль фразы в группе",
                           "Сообщение": f"Дубль фразы в группе {gid}: {phr} ({cnt})",
                           "ID группы": gid})

    return issues, ads, images, phrases


def _build_passport(file_name, sheet_name, meta, ads):
    groups, phrases, ad_ids, regions, images = set(), set(), set(), set(), set()
    for a in ads:
        if a.get("ID группы"): groups.add(a["ID группы"])
        if a.get("ID фразы"): phrases.add(a["ID фразы"])
        if a.get("ID объявления"): ad_ids.add(a["ID объявления"])
        if a.get("Регион"): regions.add(a["Регион"])
        for k, v in a.items():
            if (k.startswith("Изображение")
                    or k.startswith("Комб.Изображение")
                    or k == "Креатив") and v:
                images.add(v)

    return {
        "Файл": file_name,
        "Лист": sheet_name,
        "Тип кампании": meta.get("Тип кампании", ""),
        "Места показа": meta.get("Места показа", ""),
        "№ заказа": meta.get("№ заказа", ""),
        "Валюта": meta.get("Валюта", ""),
        "Объект продвижения": meta.get("Объект продвижения", ""),
        "Организация из Яндекс Бизнеса": meta.get("Организация из Яндекс Бизнеса", ""),
        "Номер телефона": meta.get("Номер телефона", ""),
        "Минус-фразы на кампанию (симв.)": len(meta.get("Минус-фразы на кампанию", "")),
        "Кол-во групп": len(groups),
        "Кол-во фраз": len(phrases),
        "Кол-во объявлений": len(ad_ids),
        "Уникальных регионов": len(regions),
        "Уникальных изображений": len(images),
        "Регионы": "; ".join(sorted(regions))[:500],
    }


def _analyze_images(entries):
    by_url: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        u = e["URL изображения"]
        rec = by_url.setdefault(u, {"URL изображения": u, "Использований": 0,
                                    "Колонки": set(), "ID объявлений": set(), "Файлы": set()})
        rec["Использований"] += 1
        rec["Колонки"].add(e["Колонка"])
        if e["ID объявления"]: rec["ID объявлений"].add(e["ID объявления"])
        rec["Файлы"].add(e["Файл"])
    rows = []
    for u, r in sorted(by_url.items(), key=lambda x: -x[1]["Использований"]):
        rows.append({
            "URL изображения": u,
            "Использований": r["Использований"],
            "Дубль": "Да" if r["Использований"] > 1 else "Нет",
            "Колонки": ", ".join(sorted(r["Колонки"])),
            "Объявлений": len(r["ID объявлений"]),
            "ID объявлений (пример)": ", ".join(sorted(r["ID объявлений"])[:20]),
            "Файлы": ", ".join(sorted(r["Файлы"])),
        })
    return rows


def audit_path(path: str) -> Dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        files: List[Path] = []
        for ext in ("*.xlsx", "*.xls", "*.csv", "*.txt"):
            files.extend(sorted(p.glob(ext)))
    elif p.exists():
        files = [p]
    else:
        return {"summary": {"error": "path_not_found"}, "issues": [], "passports": [],
                "ads": [], "images": [], "phrases": [], "minus_phrases": [],
                "files": [], "regions": [], "dictionary": None}

    all_issues, passports, all_ads, all_images, all_phrases = [], [], [], [], []
    all_minus = []
    files_report, regions_all = [], []
    dictionary_raw = None
    meta_all = []

    for f in files:
        try:
            meta: Dict[str, str] = {}
            sheets_report = []
            local_regions: List[str] = []

            for sheet_name, df_raw in _iter_sheets(f):
                low = sheet_name.lower()
                if "регион" in low:
                    local_regions = _parse_regions(df_raw)
                    sheets_report.append({"Лист": sheet_name, "Роль": "Регионы",
                                          "Строк": int(len(df_raw))})
                elif "словар" in low:
                    dictionary_raw = df_raw.copy()
                    sheets_report.append({"Лист": sheet_name, "Роль": "Словарь",
                                          "Строк": int(len(df_raw))})

            for sheet_name, df_raw in _iter_sheets(f):
                low = sheet_name.lower()
                if "регион" in low or "словар" in low:
                    continue
                if not meta:
                    meta = _read_metadata(df_raw)

                campaign_minus = _parse_minus_phrases(meta.get("Минус-фразы на кампанию", ""))
                for mp in campaign_minus:
                    all_minus.append({
                        "Файл": f.name,
                        "Тип кампании": meta.get("Тип кампании", ""),
                        "Уровень": "Кампания",
                        "ID группы": "",
                        "Название группы": "",
                        "Номер группы": "",
                        "Минус-фраза": mp,
                        "Слов": _minus_word_count(mp),
                        "Длина": len(mp),
                    })
                if len(meta.get("Минус-фразы на кампанию", "")) > MINUS_CAMPAIGN_MAX:
                    all_issues.append({
                        "Файл": f.name, "Лист": sheet_name, "Уровень": "Ошибка",
                        "Тип проблемы": "Минус-фразы кампании слишком длинные",
                        "Сообщение": f"Минус-фразы на кампанию > {MINUS_CAMPAIGN_MAX}: "
                                     f"{len(meta.get('Минус-фразы на кампанию', ''))}"
                    })

                issues, ads, imgs, ph = _audit_texts(df_raw, meta, f.name, sheet_name)
                all_issues.extend(issues)
                all_ads.extend(ads)
                all_images.extend(imgs)
                all_phrases.extend(ph)

                main_idx, sub_idx = _find_header(df_raw)
                if main_idx is not None:
                    cmap = {c: i for i, c in enumerate(_unique_names(
                        _build_columns(df_raw, main_idx, sub_idx))) if c}
                    gi = cmap.get("ID группы")
                    gni = cmap.get("Название группы")
                    gnumi = cmap.get("Номер группы")
                    mgi = cmap.get("Минус-фразы на группу")
                    data_start = (sub_idx + 1) if sub_idx is not None else (main_idx + 1)
                    seen_groups = set()
                    for i in range(data_start, len(df_raw)):
                        row = df_raw.iloc[i].fillna("").tolist()
                        gid = _clean(row[gi]) if gi is not None and gi < len(row) else ""
                        if not gid or gid in seen_groups:
                            continue
                        seen_groups.add(gid)
                        mg = _clean(row[mgi]) if mgi is not None and mgi < len(row) else ""
                        for mp in _parse_minus_phrases(mg):
                            all_minus.append({
                                "Файл": f.name,
                                "Тип кампании": meta.get("Тип кампании", ""),
                                "Уровень": "Группа",
                                "ID группы": gid,
                                "Название группы": _clean(row[gni]) if gni is not None and gni < len(row) else "",
                                "Номер группы": _clean(row[gnumi]) if gnumi is not None and gnumi < len(row) else "",
                                "Минус-фраза": mp,
                                "Слов": _minus_word_count(mp),
                                "Длина": len(mp),
                            })

                sheets_report.append({"Лист": sheet_name, "Роль": "Тексты",
                                      "Строк": int(len(df_raw)), "Проблем": len(issues)})
                if ads:
                    passports.append(_build_passport(f.name, sheet_name, meta, ads))

            files_report.append({
                "Файл": f.name, "Листы": sheets_report, "Метаданные": meta,
                "Регионов в справочнике": len(local_regions),
            })
            for r in local_regions:
                if r not in regions_all:
                    regions_all.append(r)
            if meta:
                meta_all.append({"Файл": f.name, **meta})
        except Exception as e:
            all_issues.append({"Файл": f.name, "Уровень": "Ошибка",
                               "Тип проблемы": "Ошибка чтения", "Сообщение": str(e)})

    summary = {
        "Файлов": len(files),
        "Всего проблем": len(all_issues),
        "Ошибок": sum(1 for i in all_issues if i.get("Уровень") == "Ошибка"),
        "Предупреждений": sum(1 for i in all_issues if i.get("Уровень") == "Предупреждение"),
        "Всего объявлений": len(all_ads),
        "Всего групп": len({a.get("ID группы") for a in all_ads if a.get("ID группы")}),
        "Уникальных изображений": len({e["URL изображения"] for e in all_images}),
        "Регионов в справочнике": len(regions_all),
        "Всего фраз": len(all_phrases),
        "Всего минус-фраз": len(all_minus),
    }
    return {
        "summary": summary,
        "issues": all_issues,
        "passports": passports,
        "ads": all_ads,
        "images": _analyze_images(all_images),
        "phrases": all_phrases,
        "minus_phrases": all_minus,
        "files": files_report,
        "meta": meta_all,
        "regions": regions_all,
        "dictionary": dictionary_raw,
    }


def export_report(result: Dict[str, Any],
                  out_xlsx: str = "audit_report.xlsx",
                  out_issues_csv: str = "audit_report_issues.csv",
                  out_ads_csv: str = "audit_report_ads.csv",
                  out_phrases_csv: str = "audit_report_phrases.csv",
                  out_minus_csv: str = "audit_report_minus.csv") -> Dict[str, str]:
    s = result["summary"]
    df_summary = pd.DataFrame(list(s.items()), columns=["Показатель", "Значение"])
    df_toc = pd.DataFrame(SHEET_DESCRIPTIONS, columns=["Раздел", "Описание"])

    df_issues = pd.DataFrame(result["issues"])
    if df_issues.empty:
        df_issues = pd.DataFrame(columns=["Файл", "Лист", "Строка", "Уровень",
                                          "Тип проблемы", "Сообщение",
                                          "ID группы", "ID объявления", "ID фразы",
                                          "Тип кампании"])

    df_passports = pd.DataFrame(result["passports"]) if result["passports"] else pd.DataFrame()
    df_ads = pd.DataFrame(result["ads"]) if result["ads"] else pd.DataFrame()
    df_images = pd.DataFrame(result["images"]) if result["images"] else pd.DataFrame()
    df_phrases = pd.DataFrame(result["phrases"]) if result["phrases"] else pd.DataFrame()
    df_minus = pd.DataFrame(result["minus_phrases"]) if result["minus_phrases"] else pd.DataFrame()

    rows_files = []
    for fr in result["files"]:
        for sh in fr["Листы"]:
            rows_files.append({
                "Файл": fr["Файл"], "Лист": sh["Лист"], "Роль": sh["Роль"],
                "Строк": sh["Строк"], "Проблем": sh.get("Проблем", ""),
            })
    df_files = pd.DataFrame(rows_files)

    df_meta = pd.DataFrame(result["meta"]) if result["meta"] else pd.DataFrame(columns=["Файл"])

    df_regions = pd.DataFrame({"Регион": result["regions"]}) \
        if result["regions"] else pd.DataFrame(columns=["Регион"])

    df_dict = result["dictionary"] if result["dictionary"] is not None \
        else pd.DataFrame()

    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as w:
        df_toc.to_excel(w, sheet_name="Оглавление", index=False)
        df_summary.to_excel(w, sheet_name="Сводка", index=False)
        df_issues.to_excel(w, sheet_name="1_Аудит_проблемы", index=False)
        df_passports.to_excel(w, sheet_name="2_Паспорт_кампаний", index=False)
        df_ads.to_excel(w, sheet_name="3_Объявления", index=False)
        df_images.to_excel(w, sheet_name="4_Изображения", index=False)
        df_phrases.to_excel(w, sheet_name="5_Фразы", index=False)
        df_minus.to_excel(w, sheet_name="6_Минус_фразы", index=False)
        df_files.to_excel(w, sheet_name="Файлы_и_листы", index=False)
        df_meta.to_excel(w, sheet_name="Метаданные", index=False)
        df_regions.to_excel(w, sheet_name="Справочник_Регионы", index=False)
        if not df_dict.empty:
            df_dict.to_excel(w, sheet_name="Справочник_Поля", index=False, header=False)

    df_issues.to_csv(out_issues_csv, index=False, encoding="utf-8-sig")
    if not df_ads.empty:
        df_ads.to_csv(out_ads_csv, index=False, encoding="utf-8-sig")
    if not df_phrases.empty:
        df_phrases.to_csv(out_phrases_csv, index=False, encoding="utf-8-sig")
    if not df_minus.empty:
        df_minus.to_csv(out_minus_csv, index=False, encoding="utf-8-sig")

    return {
        "xlsx": out_xlsx,
        "issues_csv": out_issues_csv,
        "ads_csv": out_ads_csv,
        "phrases_csv": out_phrases_csv,
        "minus_csv": out_minus_csv,
    }


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Аудит выгрузок Яндекс.Директ Коммандера")
    ap.add_argument("path", help="Файл XLSX/XLS или папка")
    ap.add_argument("--out-xlsx", default="audit_report.xlsx")
    ap.add_argument("--out-issues", default="audit_report_issues.csv")
    ap.add_argument("--out-ads", default="audit_report_ads.csv")
    ap.add_argument("--out-phrases", default="audit_report_phrases.csv")
    ap.add_argument("--out-minus", default="audit_report_minus.csv")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = audit_path(args.path)
    paths = export_report(result, args.out_xlsx, args.out_issues, args.out_ads,
                          args.out_phrases, args.out_minus)

    if args.json:
        print(json.dumps({**result, "export": paths},
                         ensure_ascii=False, indent=2, default=str))
    else:
        s = result["summary"]
        print(f"Файлов: {s['Файлов']} | объявлений: {s['Всего объявлений']} | "
              f"групп: {s['Всего групп']} | проблем: {s['Всего проблем']} "
              f"(ошибок: {s['Ошибок']}, предупреждений: {s['Предупреждений']})")
        print(f"Excel: {paths['xlsx']}")
        print(f"CSV проблемы: {paths['issues_csv']}")
        print(f"CSV объявления: {paths['ads_csv']}")
        print(f"CSV фразы: {paths['phrases_csv']}")
        print(f"CSV минус-фразы: {paths['minus_csv']}")

@mcp.tool()
def audit_direct_commander_files(path: str, json_output: bool = False) -> str:
    """
    Аудит выгрузок Яндекс.Директ Коммандера (XLSX/XLS).
    path — файл или папка с выгрузками.
    json_output — если True, вернуть JSON.
    """
    import json as _json
    result = audit_path(path)
    export_report(result)
    if json_output:
        return _json.dumps(result, ensure_ascii=False, indent=2, default=str)
    s = result["summary"]
    return (
        f"Файлов: {s['Файлов']} | объявлений: {s['Всего объявлений']} | "
        f"групп: {s['Всего групп']} | проблем: {s['Всего проблем']} "
        f"(ошибок: {s['Ошибок']}, предупреждений: {s['Предупреждений']})"
    )



if __name__ == "__main__":
    main()
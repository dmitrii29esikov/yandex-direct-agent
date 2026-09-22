"""
Sloj diagnozov: nadgrupka nad nahodkami.

Nahodka otvechaet "chto ne tak". Diagnoz otvechaet "pochemu eto meshaet
konversiyam i chto delat' po poryadku". Imenno etot sloj chitaet chelovek.
"""

from .base import Diagnosis

MAX_GENERIC = 12


def _by_code(findings):
    out = {}
    for f in findings:
        out.setdefault(f.code, []).append(f)
    return out


def _actions_for(findings_by_code, diag_codes):
    actions = []
    seen = set()
    for code in diag_codes:
        for f in findings_by_code.get(code, []):
            if f.fix and f.fix not in seen:
                seen.add(f.fix)
                actions.append(f.fix)
    return actions


def _impact(findings, bonus=0):
    if not findings:
        return 0
    base = max(f.impact for f in findings)
    return min(100, base + bonus + 3 * (len(findings) - 1))


def _severity(findings):
    order = {"error": 3, "warning": 2, "info": 1}
    return max((f.severity for f in findings), key=lambda s: order.get(s, 0))


def diagnose(account: str, findings, target_cpa=None) -> list:
    """Sobiraem diagnozy iz nahodok. Spisok uzhe otsortirovan po impact."""
    codes = _by_code(findings)
    diagnoses: list = []
    used_codes = set()

    def take(*code_list):
        result = []
        for c in code_list:
            result.extend(codes.get(c, []))
        return result

    # 1. Rashod est', konversij net, i prichiny v cel yah / razmetke.
    spend_no_conv = codes.get("DIRECT.SPEND_NO_CONVERSION", [])
    blockers = take("METRICA.NO_GOALS", "METRICA.NO_DATA", "CROSS.GOALS_MISSING_IN_DIRECT")
    if spend_no_conv:
        related = spend_no_conv + blockers
        if blockers:
            diagnoses.append(Diagnosis(
                key="DIAG.SPEND_WITHOUT_LEARNABLE_STRATEGY",
                title="Расход есть, конверсий нет: автостратегия не может обучаться",
                impact=_impact(related, bonus=10),
                severity="error",
                account=account,
                contour="cross",
                summary=(
                    f"{len(spend_no_conv)} кампаний расходуют бюджет без единой конверсии, "
                    f"и это не случайность: у аккаунта нет работающей связки "
                    f"«счётчик → цель → кампания». Автостратегия Директа в такой "
                    f"конфигурации слепа — она не знает, что считать результатом."
                ),
                impact_text=(
                    "Каждый день в таком виде — это деньги без отдачи, и алгоритм "
                    "учится на пустом сигнале, постепенно ухудшая показы."
                ),
                actions=[
                    "Проверить код счётчика Метрики на сайте (валидатор Метрики)",
                    "Убедиться, что на счётчике созданы цели и они срабатывают",
                    "Передать цель в кампанию Директа и выбрать её целью автостратегии",
                    "Проверить поисковые запросы: часть трафика может быть нецелевой",
                ],
                finding_codes=sorted({f.code for f in related}),
                object_ids=[f.object_id for f in spend_no_conv],
                money=sum(f.money or 0 for f in spend_no_conv) or None,
            ))
            used_codes.update({"DIRECT.SPEND_NO_CONVERSION", "METRICA.NO_GOALS",
                              "METRICA.NO_DATA", "CROSS.GOALS_MISSING_IN_DIRECT"})
        else:
            diagnoses.append(Diagnosis(
                key="DIAG.SPEND_WITHOUT_CONVERSION",
                title="Расход есть, конверсий нет",
                impact=_impact(spend_no_conv, bonus=5),
                severity="error",
                account=account,
                contour="direct",
                summary=(
                    f"{len(spend_no_conv)} кампаний израсходовали бюджет без конверсий. "
                    f"Цели и связка выглядят настроенными, значит вопрос скорее "
                    f"в трафике или в ставках."
                ),
                impact_text="Прямая потеря бюджета: платим за клики, которые не превращаются в заявки.",
                actions=[
                    "Разобрать поисковые запросы и добавить минус-фразы",
                    "Проверить релевантность объявлений и посадочной страницы",
                    "Ограничить неэффективные площадки в РСЯ",
                ],
                finding_codes=["DIRECT.SPEND_NO_CONVERSION"],
                object_ids=[f.object_id for f in spend_no_conv],
                money=sum(f.money or 0 for f in spend_no_conv) or None,
            ))
            used_codes.add("DIRECT.SPEND_NO_CONVERSION")

    # 2. Nikto ne pokazyvaetsya.
    no_active = codes.get("DIRECT.NO_ACTIVE_CAMPAIGNS", [])
    if no_active:
        diagnoses.append(Diagnosis(
            key="DIAG.NO_ACTIVE_CAMPAIGNS",
            title="Аккаунт не показывается: ни одной запущенной кампании",
            impact=_impact(no_active, bonus=5),
            severity="error",
            account=account,
            contour="direct",
            summary=(
                "Все кампании остановлены, приостановлены или в архиве. "
                "Пока это так, никакие улучшения настроек не дадут результата."
            ),
            impact_text="Всё остальное в этом отчёте имеет смысл только после запуска кампаний.",
            actions=[
                "Определить, какие кампании должны работать сейчас",
                "Снять их с паузы или вывести из архива",
                "После запуска снять новый аудит: набор проблем изменится",
            ],
            finding_codes=["DIRECT.NO_ACTIVE_CAMPAIGNS"],
            object_ids=[f.object_id for f in no_active],
        ))
        used_codes.add("DIRECT.NO_ACTIVE_CAMPAIGNS")

    # 3. Teksty ne prohodyat po dline.
    text_len = codes.get("DIRECT.TEXT_LENGTH", [])
    if text_len:
        total_ads = sum(f.evidence.get("объявлений с превышением", 0) for f in text_len)
        diagnoses.append(Diagnosis(
            key="DIAG.TEXT_LENGTH",
            title="Тексты объявлений не проходят по длине",
            impact=_impact(text_len, bonus=5),
            severity="error",
            account=account,
            contour="direct",
            summary=(
                f"Объявлений с превышением: {total_ads} в {len(text_len)} кампаниях. "
                f"Лимиты Директа: заголовок 56, текст 81 символ. Такие объявления "
                f"не показываются, то есть часть групп работает вслепую."
            ),
            impact_text="Это механический блокер: пока тексты длиннее нормы, объявления простаивают.",
            actions=[
                "Автоматически сократить заголовки до 56 и тексты до 81 символа (правится скриптом)",
                "Проверить, не пострадал ли смысл после сокращения",
                "Отправить исправленные объявления на модерацию",
            ],
            finding_codes=["DIRECT.TEXT_LENGTH"],
            object_ids=[f.object_id for f in text_len],
        ))
        used_codes.add("DIRECT.TEXT_LENGTH")

    # 4. Razmetka pod voprosom.
    tracking = take("METRICA.CODE_STATUS", "METRICA.LOW_ACTIVITY")
    if tracking:
        diagnoses.append(Diagnosis(
            key="DIAG.TRACKING_QUALITY",
            title="Качество данных Метрики под вопросом",
            impact=_impact(tracking),
            severity=_severity(tracking),
            account=account,
            contour="metrica",
            summary=(
                "Состояние кода счётчика или активность вызывают вопросы. "
                "Пока это не проверено, любые выводы по конверсиям предварительные."
            ),
            impact_text="Риск не в деньгах напрямую, а в том, что решения будут приниматься по неверным данным.",
            actions=[
                "Проверить установку кода счётчика валидатором Метрики",
                "Убедиться, что цели фиксируются (тестовое достижение)",
                "Перепроверять выводы по конверсиям до устранения",
            ],
            finding_codes=sorted({f.code for f in tracking}),
            object_ids=[f.object_id for f in tracking],
        ))
        used_codes.update({"METRICA.CODE_STATUS", "METRICA.LOW_ACTIVITY"})

    # 5. Byudzhet ne ogranichen po dnyam.
    no_budget = codes.get("DIRECT.NO_DAILY_BUDGET", [])
    if no_budget:
        diagnoses.append(Diagnosis(
            key="DIAG.NO_DAILY_BUDGET",
            title="Дневной бюджет не задан",
            impact=_impact(no_budget),
            severity="warning",
            account=account,
            contour="direct",
            summary=(
                f"{len(no_budget)} кампаний без дневного бюджета: недельный бюджет "
                f"может уйти за один день, после чего показы встанут до конца недели."
            ),
            impact_text="Неравномерные показы: вы теряете клики в те дни, когда бюджет уже выбран.",
            actions=["Задать дневной бюджет и режим распределения внутри недели"],
            finding_codes=["DIRECT.NO_DAILY_BUDGET"],
            object_ids=[f.object_id for f in no_budget],
        ))
        used_codes.add("DIRECT.NO_DAILY_BUDGET")

    # 6. Dorogaya konversiya.
    cpa = codes.get("DIRECT.CPA_ABOVE_TARGET", [])
    if cpa:
        diagnoses.append(Diagnosis(
            key="DIAG.CPA_ABOVE_TARGET",
            title="Цена конверсии выше целевой",
            impact=_impact(cpa),
            severity="warning",
            account=account,
            contour="direct",
            summary=(
                f"{len(cpa)} кампаний дают конверсию дороже целевой цены"
                + (f" ({target_cpa:.0f} ₽)" if target_cpa else "")
                + ". Кампании работают, но не в целевой экономике."
            ),
            impact_text="Переплата за каждую заявку при работающей воронке.",
            actions=[
                "Снизить ставки или перейти на автостратегию с целевой ценой конверсии",
                "Исключить площадки и запросы с нулевыми конверсиями",
                "Проверить, не съедает ли бюджет низкоконверсионный сегмент",
            ],
            finding_codes=["DIRECT.CPA_ABOVE_TARGET"],
            object_ids=[f.object_id for f in cpa],
            money=sum(f.money or 0 for f in cpa) or None,
        ))
        used_codes.add("DIRECT.CPA_ABOVE_TARGET")

    # 7. Moderatsiya.
    rejected = take("DIRECT.REJECTED_ADS", "DIRECT.STATUS_PAYMENT")
    if rejected:
        diagnoses.append(Diagnosis(
            key="DIAG.MODERATION",
            title="Объявления не проходят модерацию или оплату",
            impact=_impact(rejected, bonus=5),
            severity="error",
            account=account,
            contour="direct",
            summary="Часть объявлений заблокирована модерацией или у аккаунта проблема с оплатой.",
            impact_text="Заблокированные объявления не показываются — группы работают неполным составом.",
            actions=[
                "Исправить причины отклонения и отправить на повторную модерацию",
                "Проверить статус оплаты аккаунта",
            ],
            finding_codes=sorted({f.code for f in rejected}),
            object_ids=[f.object_id for f in rejected],
        ))
        used_codes.update({"DIRECT.REJECTED_ADS", "DIRECT.STATUS_PAYMENT"})

    # 8. Razmetka YTM.
    ytm = take("YTM.TAGS_WITHOUT_TRIGGERS", "YTM.TRIGGERS_UNUSED",
               "YTM.EMPTY_CONTAINER", "CROSS.CONTAINER_COUNTER_MISMATCH")
    if ytm:
        diagnoses.append(Diagnosis(
            key="DIAG.YTM_BROKEN",
            title="Разметка Tag Manager не срабатывает",
            impact=_impact(ytm),
            severity=_severity(ytm),
            account=account,
            contour="ytm",
            summary=(
                f"{len(ytm)} проблем в контейнерах Tag Manager: теги без триггеров, "
                f"неиспользуемые триггеры, непривязанные контейнеры."
            ),
            impact_text=(
                "Сломанная разметка = потерянные события. Это прямо искажает цели "
                "Метрики, а значит и обучение автостратегий."
            ),
            actions=[
                "Через API Tag Manager правки недоступны — исправления только в интерфейсе",
                "Привязать триггеры к тегам",
                "Удалить неиспользуемые триггеры и переменные",
            ],
            finding_codes=sorted({f.code for f in ytm}),
            object_ids=[f.object_id for f in ytm],
        ))
        used_codes.update({"YTM.TAGS_WITHOUT_TRIGGERS", "YTM.TRIGGERS_UNUSED",
                           "YTM.EMPTY_CONTAINER", "CROSS.CONTAINER_COUNTER_MISMATCH"})

    # 9. Ostatki: chtoby nichego znachimogo ne poteryalos'.
    remainder = [f for f in findings
                 if f.code not in used_codes and f.severity in ("error", "warning")]
    grouped = {}
    for f in remainder:
        grouped.setdefault(f.code, []).append(f)

    ordered = sorted(grouped.items(),
                     key=lambda kv: max(f.impact for f in kv[1]), reverse=True)
    for code, items in ordered[:MAX_GENERIC]:
        if max(f.impact for f in items) < 25:
            break
        prefix = f"Затронуто объектов: {len(items)}. " if len(items) > 1 else ""
        diagnoses.append(Diagnosis(
            key=f"DIAG.{code}",
            title=items[0].title,
            impact=_impact(items),
            severity=_severity(items),
            account=account,
            contour=items[0].contour,
            summary=f"{prefix}{items[0].detail}",
            impact_text="Влияние локальное, но исправление дешёвое.",
            actions=_actions_for(codes, [code])[:3],
            finding_codes=[code],
            object_ids=[f.object_id for f in items],
            money=sum(f.money or 0 for f in items) or None,
        ))

    diagnoses.sort(key=lambda d: d.impact, reverse=True)
    return diagnoses

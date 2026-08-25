from __future__ import annotations

import re

from .config import DEFAULT_FOLDER, FOLDER_STRUCTURE
from .types import Classification


_KEYWORD_MAP: list[tuple[str, list[str]]] = [
    ("01_УПРАВЛЕНИЕ ПРОЕКТОМ", [
        "график", "план проекта", "протокол совещ", "отчёт о статусе",
        "отчет о статусе", "статус проекта", "план работ", "календарный план",
        "график работ", "совещание", "гпр",
    ]),
    ("02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ", [
        "договор", "доп соглашение", "дополнительное соглашение",
        "доверенность", "контракт", "соглашение", "юридичес",
        "претензия", "гарантия",
    ]),
    ("03_ФИНАНСЫ И СМЕТЫ", [
        "смета", "бюджет", "счет", "счёт", "коммерческое предложение",
        "коммерческое", "кп", "оплата", "платеж", "платёж",
        "финанс", "стоимость", "цена", "акт сверки",
    ]),
    ("04_ПРОЕКТИРОВАНИЕ", [
        "чертеж", "чертёж", "спецификация",
        "проектное решение", ".dwg", ".dxf", "проектная документация",
        "рабочая документация", "схема", "планировка",
    ]),
    ("05_ЗАКУПКИ И ПОСТАВКИ", [
        "заявка на закупку", "заказ поставщику", "поставщик", "закупка",
        "спецификация оборудования", "поставка", "оборудование",
        "материал", "комплектация",
    ]),
    ("06_ПОДРЯДЧИКИ И КОНТРАГЕНТЫ", [
        "анкета контрагента", "реквизиты", "досье подрядчика",
        "контрагент", "подрядчик", "субподрядчик",
    ]),
    ("07_ПЕРЕПИСКА И СОГЛАСОВАНИЯ", [
        "письмо", "переписка", "согласование", "протокол разногласий",
        "запрос", "ответ", "уведомление", "служебная записка",
    ]),
    ("08_ИСПОЛНЕНИЕ И ОТЧЁТНОСТЬ", [
        "акт выполненных работ", "акт", "фотоотчет", "фотоотчёт",
        "исполнительная документация", "исполнительная схема",
        "отчет", "отчёт", "выполнение", "исполнение",
    ]),
    ("09_ЗАКРЫТИЕ ПРОЕКТА", [
        "итоговый отчет", "итоговый отчёт", "финальный комплект",
        "закрытие проекта", "закрывающие", "итоговый", "финальный",
    ]),
]

_VALID_FOLDERS = {name for name, _ in FOLDER_STRUCTURE}

# High-specificity compound filename rules.
#
# These run before the generic keyword map because words such as "расчет"
# are semantically ambiguous on their own: a calculation can be financial
# or engineering. Keep these rules deliberately narrow and corpus-backed.
_COMPOUND_RULES: list[tuple[str, re.Pattern[str], float, str]] = [
    (
        "03_ФИНАНСЫ И СМЕТЫ",
        re.compile(
            r"(?<![0-9a-zа-яё])расч[её]т\s+стоимост(?:ь|и)?"
            r"(?![0-9a-zа-яё])",
            re.IGNORECASE,
        ),
        0.90,
        "Обнаружено финансовое сочетание «расчет стоимости».",
    ),
    (
        "03_ФИНАНСЫ И СМЕТЫ",
        re.compile(
            r"(?<![0-9a-zа-яё])расч[её]т\s+аванса?"
            r"(?![0-9a-zа-яё])",
            re.IGNORECASE,
        ),
        0.90,
        "Обнаружено финансовое сочетание «расчет аванса».",
    ),
    (
        "03_ФИНАНСЫ И СМЕТЫ",
        re.compile(
            r"(?<![0-9a-zа-яё])сметн[а-яё]*\s+расч[её]т"
            r"(?![0-9a-zа-яё])",
            re.IGNORECASE,
        ),
        0.90,
        "Обнаружено финансовое сочетание «сметный расчет».",
    ),
    (
        "03_ФИНАНСЫ И СМЕТЫ",
        re.compile(
            r"(?<![0-9a-zа-яё])взаимн[а-яё]*\s+расч[её]т[а-яё]*"
            r"(?![0-9a-zа-яё])",
            re.IGNORECASE,
        ),
        0.90,
        "Обнаружено финансовое сочетание «взаимные расчеты».",
    ),
    (
        "04_ПРОЕКТИРОВАНИЕ",
        re.compile(
            r"(?<![0-9a-zа-яё])расч[её]т\s+токов"
            r"(?![0-9a-zа-яё])",
            re.IGNORECASE,
        ),
        0.90,
        "Обнаружено инженерное сочетание «расчет токов».",
    ),
    (
        "04_ПРОЕКТИРОВАНИЕ",
        re.compile(
            r"(?<![0-9a-zа-яё])расч[её]т\s+уставок"
            r"(?![0-9a-zа-яё])",
            re.IGNORECASE,
        ),
        0.90,
        "Обнаружено инженерное сочетание «расчет уставок».",
    ),
    (
        "04_ПРОЕКТИРОВАНИЕ",
        re.compile(
            r"(?<![0-9a-zа-яё])расч[её]т\s+рза"
            r"(?![0-9a-zа-яё])",
            re.IGNORECASE,
        ),
        0.90,
        "Обнаружено инженерное сочетание «расчет РЗА».",
    ),
]

_DATE_PATTERNS = [
    re.compile(r"(20\d{2})[-_.](\d{2})[-_.](\d{2})"),
    re.compile(r"(\d{2})[-_.](\d{2})[-_.](20\d{2})"),
]

_VERSION_PATTERN = re.compile(
    r"(?:v|версия|вер)\s?(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Common Russian project-document section codes.
# These are deliberately conservative: they only indicate
# "project/design documentation", not a more specific lifecycle section.
_PROJECT_CODE_PATTERN = re.compile(
    r"(?:^|[\s._()\-])"
    r"(?:АР|ОПЗ|ИОС(?:\d+(?:\.\d+)?)?|КР|КЖ|КМ|ОВ|ВК|"
    r"ЭОМ|ЭС|СС|АСДУ|СКТП)"
    r"(?:$|[\s._()\-])",
)

# A few two-letter project codes are also ordinary sequences of spoken
# letter names in company/person names. Reject those obvious contexts.
_PROJECT_CODE_FALSE_POSITIVE_PATTERNS = [
    re.compile(
        r"(?<![0-9A-Za-zА-Яа-яЁё])АЙ\s+ЭС\s+ТИ(?![0-9A-Za-zА-Яа-яЁё])",
        re.IGNORECASE,
    ),
]


def _keyword_matches(text: str) -> list[str]:
    """
    Match classifier keywords without treating arbitrary substrings as words.

    Examples:
      "ответ" matches "Ответ заказчику.pdf"
      "ответ" does NOT match "ответственный"
      "акт" matches "Акт №15.pdf"
      "акт" does NOT match "контракт"

    Phrase fragments intentionally present in _KEYWORD_MAP, such as
    "юридичес" and "протокол совещ", retain prefix-style matching.
    """
    lower = text.lower()
    matches: list[str] = []

    prefix_fragments = {
        "юридичес",
        "финанс",
        "протокол совещ",
    }

    def keyword_matches(keyword: str) -> bool:
        keyword = keyword.lower()

        # File-extension markers are intentionally literal.
        if keyword.startswith("."):
            return keyword in lower

        # Explicit morphological/phrase prefixes from the rule table.
        if keyword in prefix_fragments:
            pattern = (
                r"(?<![0-9a-zа-яё])"
                + re.escape(keyword).replace(r"\ ", r"\s+")
            )
            return re.search(pattern, lower, re.IGNORECASE) is not None

        # Normal keywords and phrases must be bounded on both sides.
        # This prevents e.g. "ответ" -> "ответственный"
        # and "акт" -> "контракт".
        pattern = (
            r"(?<![0-9a-zа-яё])"
            + re.escape(keyword).replace(r"\ ", r"\s+")
            + r"(?![0-9a-zа-яё])"
        )
        return re.search(pattern, lower, re.IGNORECASE) is not None

    for folder, keywords in _KEYWORD_MAP:
        if any(keyword_matches(keyword) for keyword in keywords):
            matches.append(folder)

    return list(dict.fromkeys(matches))


def classify(
    filename: str,
    confirmed_rules: list[dict] | None = None,
    context: str | None = None,
    content: str | None = None,
) -> Classification:
    # Explicit user-confirmed rules remain the strongest signal.
    for rule in confirmed_rules or []:
        pattern = rule.get("pattern") or {}
        keyword = pattern.get("filename_contains")
        folder = (rule.get("action") or {}).get("folder")

        if (
            keyword
            and folder in _VALID_FOLDERS
            and keyword.lower() in filename.lower()
        ):
            return Classification(
                folder,
                0.98,
                f"Подтверждённое правило пользователя: содержит «{keyword}».",
            )

    for folder, pattern, confidence, reasoning in _COMPOUND_RULES:
        if pattern.search(filename):
            return Classification(
                folder,
                confidence,
                reasoning,
            )

    filename_matches = _keyword_matches(filename)

    if len(filename_matches) == 1:
        folder = filename_matches[0]
        return Classification(
            folder,
            0.85,
            f"Найдено правило по имени файла для раздела «{folder}».",
        )

    if len(filename_matches) > 1:
        return Classification(
            filename_matches[0],
            0.40,
            f"По имени файла подошло несколько разделов: "
            f"{', '.join(filename_matches)}.",
            True,
        )

    if content:
        content_matches = _keyword_matches(content[:50000])
        if len(content_matches) == 1:
            folder = content_matches[0]
            return Classification(
                folder,
                0.92,
                f"Раздел определён по тексту документа: «{folder}».",
            )
        if len(content_matches) > 1:
            return Classification(
                content_matches[0],
                0.60,
                "В тексте документа найдено несколько возможных разделов: "
                f"{', '.join(content_matches)}.",
                True,
            )

    # Recognize common project-document section codes such as
    # АР / ОПЗ / ИОС / КЖ / ЭОМ when filename keywords are absent.
    stem = filename.rsplit(".", 1)[0]

    project_code_match = _PROJECT_CODE_PATTERN.search(stem)
    project_code_false_positive = any(
        pattern.search(stem)
        for pattern in _PROJECT_CODE_FALSE_POSITIVE_PATTERNS
    )

    if project_code_match and not project_code_false_positive:
        return Classification(
            "04_ПРОЕКТИРОВАНИЕ",
            0.78,
            "Обнаружен шифр раздела проектной/рабочей документации.",
        )

    # Folder path is useful context, but intentionally weaker than filename.
    # This lets e.g. Scan.pdf inside /Договоры/ inherit a probable category
    # while generic media under /Музыка/ stays unresolved.
    if context:
        context_matches = _keyword_matches(context)

        if len(context_matches) == 1:
            folder = context_matches[0]
            return Classification(
                folder,
                0.70,
                f"Раздел определён по контексту исходного пути: «{folder}».",
            )

        if len(context_matches) > 1:
            return Classification(
                context_matches[0],
                0.45,
                f"В исходном пути найдено несколько возможных разделов: "
                f"{', '.join(context_matches)}.",
                True,
            )

    return Classification(
        DEFAULT_FOLDER,
        0.30,
        "Недостаточно данных для уверенной классификации.",
        True,
    )


def extract_date(filename: str) -> str | None:
    for pat in _DATE_PATTERNS:
        match = pat.search(filename)

        if not match:
            continue

        a, b, c = match.groups()

        if len(a) == 4:
            year, month, day = a, b, c
        else:
            day, month, year = a, b, c

        return f"{year}-{month}-{day}"

    return None


def extract_version(filename: str) -> str | None:
    match = _VERSION_PATTERN.search(filename)
    return f"v{match.group(1)}" if match else None

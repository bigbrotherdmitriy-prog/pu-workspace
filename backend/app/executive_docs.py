import re

KINDS = (
    "aosr", "act", "protocol", "passport", "certificate", "declaration",
    "refusal_letter", "scheme", "drawing_source", "journal", "register", "toir",
    "report", "photo", "video", "manual", "specification", "statement", "cover",
    "other", "service",
)

_SERVICE_EXTS = (".bak", ".dst", ".dwl", ".dwl2", ".tmp")
_DRAWING_EXTS = (".dwg", ".dxf")
_VIDEO_EXTS = (".mp4", ".mov", ".avi")
_PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".heic")
_EDITABLE_EXTS = (".doc", ".docx", ".odt")

_SECTION_CODES = ("ЭМ", "ОВ", "АР", "КЖ", "АСДУ", "СКТП", "ИБП")

_SECTION_RULES = (
    ("ЭМ", ("эм", "электроснаб")),
    ("ОВиК", ("ов", "овik", "сктп", "кондиц", "вентиляц")),
    ("АСДУ", ("асду", "автоматиз", "scada")),
    ("АР", ("ар", "архитект")),
    ("КЖ", ("кж", "фундамент")),
)


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[-1].lower()


def _stem(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base:
        return base.rsplit(".", 1)[0]
    return base


def _has_word(haystack: str, word: str) -> bool:
    pat = r"(?<![А-ЯЁA-Zа-яёa-z])" + re.escape(word) + r"(?![А-ЯЁA-Zа-яёa-z])"
    return re.search(pat, haystack, re.IGNORECASE) is not None


def _detect_section(path: str, name: str) -> str | None:
    text = _norm(f"{path} {name}")
    if _has_word(text, "ИБП") or "электроснаб" in text:
        return "ЭМ"
    if _has_word(text, "ЭМ") and not _has_word(text, "ЭМП"):
        return "ЭМ"
    if _has_word(text, "СКТП") or "кондиц" in text or _has_word(text, "ОВ") or "вентиляц" in text:
        return "ОВиК"
    if _has_word(text, "АСДУ") or "автоматиз" in text or "scada" in text:
        return "АСДУ"
    if _has_word(text, "АР") or "архитект" in text:
        return "АР"
    if _has_word(text, "КЖ") or "фундамент" in text:
        return "КЖ"
    return None


def _detect_kind(path: str, name: str, mime_type: str | None) -> str:
    text = _norm(f"{path} {name}")
    name_n = _norm(name)
    ext = _ext(name)
    base = _stem(name_n)

    if ext in _SERVICE_EXTS or base.startswith("~$") or base == "thumbs.db" or name_n.startswith("thumbs.db"):
        return "service"
    if ext in _DRAWING_EXTS:
        return "drawing_source"
    if ext in _VIDEO_EXTS:
        return "video"
    if "аоср" in text or ("освидетельствован" in text and "скрыт" in text):
        return "aosr"
    if "протокол" in text:
        return "protocol"
    if "тоир" in text:
        return "toir"
    if "реестр" in text:
        return "register"
    if "декларац" in text:
        return "declaration"
    if "отказн" in text:
        return "refusal_letter"
    if "паспорт" in text:
        return "passport"
    if "сертификат" in text:
        return "certificate"
    if "еаэс" in text or "eaes" in text:
        return "certificate"
    if re.match(r"^(ru|eaes|еаэс|[a-z]{2,3}\d{2})[._ -]", name_n):
        return "certificate"
    if "схем" in text and ("исполнительн" in text or "принципиальн" in text):
        return "scheme"
    if "журнал" in text:
        return "journal"
    if "отчет" in text:
        return "report"
    if "руководство" in text or "инструкц" in text:
        return "manual"
    if "спецификац" in text or "спека" in text:
        return "specification"
    if "ведомост" in text:
        return "statement"
    if "титул" in text or "обложк" in text:
        return "cover"
    if "фото" in text or "photo" in text:
        return "photo"
    if re.search(r"\bакт", text):
        return "act"
    if "схем" in text or "план" in text or (ext == ".pdf" and "проект" in text):
        return "scheme"
    if ext in _PHOTO_EXTS:
        return "photo"
    return "other"


def classify_file(path: str, name: str, mime_type: str | None = None) -> dict:
    kind = _detect_kind(path, name, mime_type)
    section = _detect_section(path, name)
    issues: list[str] = []
    if kind == "service":
        issues.append("service_file")
    text = _norm(f"{path} {name}")
    if "не готов" in text or "черновик" in text or "draft" in text:
        issues.append("draft_name")
    return {"kind": kind, "section": section, "issues": issues}


def draft_issues(text: str, mime_type: str | None, name: str) -> list[str]:
    issues: list[str] = []
    if _ext(name) in _EDITABLE_EXTS:
        issues.append("editable_format_not_signed")
    matches = re.findall(r"_{4,}|«_{2,}»", text or "")
    if len(matches) >= 5:
        issues.append("blank_fields")
    return issues


def _normalize_dedup_key(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    s = _norm(base)
    # «… (1)», «… (2)», «… копия» are copies; plain trailing numbers are serials/designations.
    s = re.sub(r"\s*\((?:\d{1,2}|копия|copy)\)\s*$", "", s)
    s = s.replace("№", " № ")
    for ch in "_-().":
        s = s.replace(ch, " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bкопия\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def duplicate_names(paths: list[str]) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = {}
    for p in paths:
        if _ext(p) in _SERVICE_EXTS:
            continue  # CAD autosaves are reported as service files, not duplicates
        key = _normalize_dedup_key(p)
        groups.setdefault(key, []).append(p)
    result: list[tuple[str, list[str]]] = []
    for key, items in groups.items():
        if len(items) >= 2:
            result.append((key, items))
    return result


_HEADER_RE = re.compile(r"(перечень|состав)\s+исполнительной\s+документации", re.IGNORECASE)
_STOP_RES = (
    re.compile(r"Гаранти"),
    re.compile(r"Требования к"),
    re.compile(r"\s\d{1,2}\.\d{1,2}\.?\s+[А-ЯЁ]"),
)
_COPIES_RE = re.compile(r"(\d+)\s*\(?[а-яё]*\)?\s*экз", re.IGNORECASE)


def parse_id_requirements(text: str) -> list[dict]:
    if not text:
        return []
    m = _HEADER_RE.search(text)
    if not m:
        return []
    tail = text[m.end():]
    stop_pos = len(tail)
    for r in _STOP_RES:
        sm = r.search(tail)
        if sm and sm.start() < stop_pos:
            stop_pos = sm.start()
    tail = tail[: min(stop_pos, 1500)]
    parts = tail.split(";")
    result: list[dict] = []
    for part in parts:
        title = part.strip(" .,;:")
        if len(title) < 5:
            continue
        copies: int | None = None
        cm = _COPIES_RE.search(title)
        if cm:
            try:
                copies = int(cm.group(1))
            except ValueError:
                copies = None
        required = "при необходимости" in _norm(title)
        ck = classify_file("", title)["kind"]
        if ck == "service":
            ck = "other"
        sect = classify_file("", title)["section"]
        result.append({
            "title": title,
            "doc_kind": ck,
            "copies": copies,
            "required_if_applicable": required,
            "section": sect,
            "evidence_quote": part.strip(" .,;:"),
        })
    return result


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def extract_id_requirements_ai(text: str) -> list[dict] | None:
    try:
        from app import gemini_analysis
    except Exception:
        return None
    try:
        if not gemini_analysis.deepseek_configured():
            return None
    except Exception:
        return None
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "copies": {"type": ["integer", "null"]},
                        "required_if_applicable": {"type": "boolean"},
                        "section": {"type": ["string", "null"]},
                        "evidence_quote": {"type": "string"},
                    },
                    "required": ["title", "evidence_quote"],
                },
            }
        },
        "required": ["items"],
    }
    system = "Найди в ТЗ/договоре перечень исполнительной документации. Каждый пункт — отдельный документ или комплект; evidence_quote — дословный фрагмент."
    try:
        raw = gemini_analysis._try_deepseek(system, text, schema)
    except Exception:
        return None
    if not raw:
        return None
    items = raw.get("items") if isinstance(raw, dict) else None
    if not items:
        return None
    norm_text = _norm_text(text)
    result: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = it.get("title") or ""
        quote = it.get("evidence_quote") or ""
        if not title:
            continue
        if _norm_text(quote) not in norm_text:
            continue
        ck = classify_file("", title)["kind"]
        if ck == "service":
            ck = "other"
        sect = classify_file("", title)["section"]
        result.append({
            "title": title.strip(),
            "doc_kind": ck,
            "copies": it.get("copies"),
            "required_if_applicable": bool(it.get("required_if_applicable")),
            "section": sect,
            "evidence_quote": quote,
        })
    if not result:
        return None
    return result


NORM_SETS: dict[str, list[dict]] = {
    "Общее": [
        {"title": "Общий журнал работ", "doc_kind": "journal", "norm_ref": "Приказ Минстроя России № 1026/пр"},
        {"title": "Реестр исполнительной документации", "doc_kind": "register", "norm_ref": "Приказ Минстроя России № 344/пр"},
        {"title": "Комплект рабочих чертежей с надписями о соответствии выполненных работ", "doc_kind": "scheme", "norm_ref": "Приказ Минстроя России № 344/пр, п. 7"},
        {"title": "Документы входного контроля материалов и оборудования", "doc_kind": "certificate", "norm_ref": "Приказ Минстроя России № 344/пр, п. 12"},
    ],
    "ЭМ": [
        {"title": "Акт технической готовности электромонтажных работ", "doc_kind": "act", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Ведомость технической документации, предъявляемой при приёмке", "doc_kind": "register", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Ведомость изменений и отступлений от проекта", "doc_kind": "register", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Ведомость электромонтажных недоделок", "doc_kind": "register", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Ведомость смонтированного электрооборудования", "doc_kind": "register", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Акт готовности к производству пусконаладочных работ", "doc_kind": "act", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Протокол проверки сопротивления изоляции кабеля на барабане", "doc_kind": "protocol", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Журнал прокладки кабелей", "doc_kind": "journal", "norm_ref": "И 1.13-07 (рекомендуемые формы); СП 76.13330.2016"},
        {"title": "Технический отчёт о приёмо-сдаточных испытаниях электроустановки", "doc_kind": "report", "norm_ref": "ГОСТ Р 50571.16-2019; ПУЭ гл. 1.8"},
        {"title": "Паспорта и сертификаты на оборудование и материалы", "doc_kind": "passport", "norm_ref": "Приказ Минстроя России № 344/пр, п. 12"},
    ],
    "ОВиК": [
        {"title": "Акты освидетельствования скрытых работ", "doc_kind": "aosr", "norm_ref": "Приказ Минстроя России № 344/пр, прил. 3"},
        {"title": "Акт испытания трубопроводов на герметичность", "doc_kind": "act", "norm_ref": "СП 73.13330.2016"},
        {"title": "Акт индивидуальных испытаний оборудования", "doc_kind": "act", "norm_ref": "СП 73.13330.2016"},
        {"title": "Паспорта на оборудование", "doc_kind": "passport", "norm_ref": "Приказ Минстроя России № 344/пр, п. 12"},
    ],
    "АСДУ": [
        {"title": "Акт технической готовности монтажных работ", "doc_kind": "act", "norm_ref": "СП 77.13330.2016"},
        {"title": "Ведомость смонтированного оборудования", "doc_kind": "register", "norm_ref": "СП 77.13330.2016"},
        {"title": "Протоколы проверки и наладки", "doc_kind": "protocol", "norm_ref": "СП 77.13330.2016"},
        {"title": "Паспорта на оборудование", "doc_kind": "passport", "norm_ref": "СП 77.13330.2016"},
    ],
    "АР": [
        {"title": "Акты освидетельствования скрытых работ", "doc_kind": "aosr", "norm_ref": "Приказ Минстроя России № 344/пр, прил. 3"},
        {"title": "Исполнительные схемы", "doc_kind": "scheme", "norm_ref": "Приказ Минстроя России № 344/пр"},
        {"title": "Сертификаты на материалы", "doc_kind": "certificate", "norm_ref": "Приказ Минстроя России № 344/пр, п. 12"},
    ],
    "КЖ": [
        {"title": "Акты освидетельствования скрытых работ", "doc_kind": "aosr", "norm_ref": "Приказ Минстроя России № 344/пр, прил. 3"},
        {"title": "Акт освидетельствования ответственных конструкций", "doc_kind": "act", "norm_ref": "Приказ Минстроя России № 344/пр, прил. 4"},
        {"title": "Исполнительные геодезические схемы", "doc_kind": "scheme", "norm_ref": "ГОСТ Р 51872-2019"},
        {"title": "Протоколы испытаний бетона", "doc_kind": "protocol", "norm_ref": "ГОСТ 18105-2018"},
    ],
}


def _empty_item(title: str, doc_kind: str, section: str | None, source: str, norm_ref: str | None) -> dict:
    return {
        "title": title,
        "doc_kind": doc_kind,
        "copies": None,
        "required_if_applicable": False,
        "section": section,
        "source": source,
        "norm_ref": norm_ref,
        "evidence_quote": None,
    }


def build_requirements(text: str, sections: list[str]) -> list[dict]:
    """ТЗ/contract list first; normative sets fill sections the ТЗ is silent on.

    When the ТЗ has its own list, normative items are added as recommended
    (required_if_applicable=True) — the contract defines what is mandatory."""
    items = extract_id_requirements_ai(text) or parse_id_requirements(text)
    result: list[dict] = []
    for it in items:
        result.append({
            "title": it.get("title", ""),
            "doc_kind": it.get("doc_kind") or "other",
            "copies": it.get("copies"),
            "required_if_applicable": bool(it.get("required_if_applicable")),
            "section": it.get("section"),
            "source": "tz",
            "norm_ref": None,
            "evidence_quote": it.get("evidence_quote"),
        })
    tz_has_list = bool(result)
    for section in sections:
        has_section = any(r["section"] == section and r["source"] == "tz" for r in result)
        if has_section:
            continue
        for norm in NORM_SETS.get(section, []):
            item = _empty_item(norm["title"], norm["doc_kind"], section, "norm", norm["norm_ref"])
            item["required_if_applicable"] = tz_has_list
            result.append(item)
    tz_kinds = {r["doc_kind"] for r in result if r["source"] == "tz"}
    for norm in NORM_SETS["Общее"]:
        if norm["doc_kind"] in tz_kinds:
            continue
        item = _empty_item(norm["title"], norm["doc_kind"], "Общее", "norm", norm["norm_ref"])
        item["required_if_applicable"] = tz_has_list
        result.append(item)
    return result

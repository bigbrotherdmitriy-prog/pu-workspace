"""Секретарь предлагает состав комплекта ИД по РД, сметам и ТЗ; человек ставит галочки."""
import re

from sqlalchemy.orm import Session

from app.executive_docs import NORM_SETS

_SECTION_ALIASES = {"ОВ": "ОВиК", "ОВ и К": "ОВиК"}

# (ключ, шаблон по названиям позиций РД и строк сметы, что предложить)
_RULES: list[dict] = [
    {"key": "cable", "label": "кабели и провода", "re": r"кабел|провод\b|провода|ввгн|вбшв|nym|витая пара|utp",
     "items": [
         ("Акты освидетельствования скрытых работ: прокладка кабельных линий", "aosr", "ЭМ", "Приказ Минстроя России № 344/пр, прил. 3"),
         ("Протоколы проверки сопротивления изоляции кабельных линий", "protocol", "ЭМ", "ПУЭ гл. 1.8"),
         ("Журнал прокладки кабелей", "journal", "ЭМ", "И 1.13-07"),
     ]},
    {"key": "earthing", "label": "заземление и молниезащита", "re": r"заземл|молниезащ|уравнивани|шина pe|шина n",
     "items": [
         ("Акты освидетельствования скрытых работ: заземляющее устройство", "aosr", "ЭМ", "Приказ Минстроя России № 344/пр, прил. 3"),
         ("Протокол проверки заземляющего устройства", "protocol", "ЭМ", "ПУЭ гл. 1.8"),
     ]},
    {"key": "panel", "label": "щиты и коммутационные аппараты", "re": r"щит|щр\b|щс\b|вру\b|гру\b|автоматическ(ий|ие) выключател|узо\b|дифавтомат",
     "items": [
         ("Протокол проверки цепи «фаза–нуль»", "protocol", "ЭМ", "ПУЭ гл. 1.8; ГОСТ Р 50571.16-2019"),
         ("Акт индивидуального испытания оборудования", "act", "ЭМ", "СП 76.13330.2016"),
     ]},
    {"key": "avr", "label": "АВР", "re": r"\bавр\b|автоматическ\w+ ввод\w* резерв",
     "items": [("Протокол проверки АВР", "protocol", "ЭМ", "ПУЭ гл. 1.8")]},
    {"key": "earth", "label": "земляные работы", "re": r"раскопк|разработк\w* грунт|грунт|траншея|траншеи|котлован|засыпк|подсыпк|песч|уплотнени|планировк|выемк",
     "items": [
         ("Акты освидетельствования скрытых работ: разработка траншей и котлованов", "aosr", "Земляные работы", "Приказ Минстроя России № 344/пр, прил. 3; СП 45.13330.2017"),
         ("Акты освидетельствования скрытых работ: устройство песчаного основания (подсыпка)", "aosr", "Земляные работы", "Приказ Минстроя России № 344/пр, прил. 3; СП 45.13330.2017"),
         ("Акты освидетельствования скрытых работ: обратная засыпка с уплотнением", "aosr", "Земляные работы", "Приказ Минстроя России № 344/пр, прил. 3; СП 45.13330.2017"),
         ("Протокол испытаний уплотнения грунта (песка) при обратной засыпке", "protocol", "Земляные работы", "СП 45.13330.2017; ГОСТ 22733-2016"),
         ("Акт освидетельствования грунтов основания", "act", "Земляные работы", "СП 45.13330.2017"),
     ]},
    {"key": "tray", "label": "лотки, короба, трубы под кабель", "re": r"лоток|лотки|короб|кабель-канал|гофр|штроб|закладн|кабельн\w+ канал",
     "items": [("Акты освидетельствования скрытых работ: монтаж кабеленесущих систем", "aosr", "ЭМ", "Приказ Минстроя России № 344/пр, прил. 3")]},
    {"key": "hvac", "label": "вентиляция и кондиционирование", "re": r"воздуховод|вентилятор|кондиционер|фанкойл|приточн|вытяжн|клапан",
     "items": [
         ("Акт испытания воздуховодов на герметичность", "act", "ОВиК", "СП 73.13330.2016"),
         ("Акт индивидуальных испытаний оборудования вентиляции", "act", "ОВиК", "СП 73.13330.2016"),
         ("Паспорта на оборудование вентиляции", "passport", "ОВиК", "Приказ Минстроя России № 344/пр, п. 12"),
     ]},
    {"key": "pipes", "label": "трубопроводы, отопление, водоснабжение", "re": r"трубопровод|труба|трубы|радиатор|отоплен|водоснабж|канализ",
     "items": [
         ("Акт гидравлического испытания трубопроводов", "act", "ОВиК", "СП 73.13330.2016"),
         ("Акты освидетельствования скрытых работ: трубопроводы", "aosr", "ОВиК", "Приказ Минстроя России № 344/пр, прил. 3"),
     ]},
    {"key": "concrete", "label": "бетон и арматура", "re": r"бетон|арматур|фундамент|монолитн",
     "items": [
         ("Акты освидетельствования скрытых работ: армирование и бетонирование", "aosr", "КЖ", "Приказ Минстроя России № 344/пр, прил. 3"),
         ("Протоколы испытаний бетона", "protocol", "КЖ", "ГОСТ 18105-2018"),
     ]},
    {"key": "fire", "label": "пожарная сигнализация и оповещение", "re": r"пожарн|оповещ|извещат|спецкабел",
     "items": [
         ("Акт монтажа системы пожарной сигнализации", "act", "АСДУ", "СП 484.1311500.2020"),
         ("Протокол проверки системы пожарной сигнализации", "protocol", "АСДУ", "СП 484.1311500.2020"),
     ]},
    {"key": "controls", "label": "автоматика и диспетчеризация", "re": r"контроллер|датчик|диспетчер|асду|шлюз",
     "items": [
         ("Протоколы проверки и наладки систем автоматики", "protocol", "АСДУ", "СП 77.13330.2016"),
         ("Ведомость смонтированного оборудования автоматики", "register", "АСДУ", "СП 77.13330.2016"),
     ]},
]


# Всегда в комплекте: ведомость оборудования, реестр ИД и документы сдачи объекта.
_ALWAYS: list[tuple[str, str, str, str, str]] = [
    ("Акт приёмки геодезической разбивочной основы", "act", "Геодезия", "СП 126.13330.2017; Приказ Минстроя России № 344/пр",
     "заказчик передаёт подрядчику знаки разбивочной основы"),
    ("Акт разбивки осей объекта", "act", "Геодезия", "СП 126.13330.2017; Приказ Минстроя России № 344/пр",
     "вынос осей в натуру перед началом работ"),
    ("Исполнительные геодезические схемы", "scheme", "Геодезия", "ГОСТ Р 51872-2019",
     "исполнительная съёмка выполненных конструкций и трасс"),
    ("Общий журнал геодезических работ", "journal", "Геодезия", "СП 126.13330.2017",
     "журнал геодезических измерений"),
    ("Ведомость смонтированного оборудования", "register", "Общее", "И 1.13-07; СП 76.13330.2016",
     "перечень оборудования, смонтированного по РД"),
    ("Реестр исполнительной документации", "register", "Общее", "Приказ Минстроя России № 344/пр",
     "обязательный реестр комплекта ИД"),
    ("Акт приёмки оборудования после индивидуального испытания", "act", "Общее", "СП 73.13330.2016; СП 76.13330.2016",
     "приёмка после индивидуальных испытаний"),
    ("Акт комплексного опробования оборудования (72 часа под нагрузкой)", "act", "Общее", "ПУЭ гл. 1.8; СП 76.13330.2016",
     "опытное (комплексное) испытание под нагрузкой 72 часа"),
    ("Акт приёмки законченных работ рабочей комиссией", "act", "Общее", "СП 68.13330.2017",
     "сдача объекта заказчику"),
    ("Акт ввода в эксплуатацию", "act", "Общее", "СП 68.13330.2017; ПТЭЭП",
     "ввод объекта в эксплуатацию"),
]


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").lower().replace("ё", "е")).strip()


def _texts(db: Session, project_id: int, object_name: str | None) -> tuple[list[str], list[str], list[str]]:
    """(названия позиций РД, названия строк смет, разделы РД)."""
    from app.models.estimates import EstimateLine
    from app.models.rd import RdSpecItem, RdVolume

    vols = db.query(RdVolume).filter(RdVolume.project_id == project_id).all()
    if object_name and any(v.object_name == object_name for v in vols):
        vols = [v for v in vols if v.object_name == object_name]
    vol_ids = [v.id for v in vols]
    rd_names: list[str] = []
    if vol_ids:
        rows = db.query(RdSpecItem.name, RdSpecItem.type_mark).filter(
            RdSpecItem.volume_id.in_(vol_ids), RdSpecItem.status != "rejected",
        ).all()
        rd_names = [f"{n or ''} {m or ''}" for n, m in rows]
    est_names = [
        n or "" for (n,) in db.query(EstimateLine.name).filter(EstimateLine.project_id == project_id).all()
    ]
    sections: list[str] = []
    for v in vols:
        sec = _SECTION_ALIASES.get((v.section or "").strip(), (v.section or "").strip())
        if sec and sec not in sections:
            sections.append(sec)
    return rd_names, est_names, sections


def _count(pattern: re.Pattern, names: list[str]) -> int:
    return sum(1 for n in names if pattern.search(n.lower().replace("ё", "е")))


def propose_kit(db: Session, project_id: int, object_name: str | None) -> dict:
    from app.models.executive_docs import IdRequirement

    rd_names, est_names, sections = _texts(db, project_id, object_name)
    proposals: dict[str, dict] = {}

    def add(title, doc_kind, section, norm_ref, reason, source):
        k = _norm_title(title)
        if k in proposals:
            if reason not in proposals[k]["reason"]:
                proposals[k]["reason"] += "; " + reason
            return
        proposals[k] = {
            "key": k, "title": title, "doc_kind": doc_kind, "section": section,
            "norm_ref": norm_ref, "reason": reason, "source": source, "checked": True,
        }

    q = db.query(IdRequirement).filter(
        IdRequirement.project_id == project_id, IdRequirement.source.in_(("tz", "kit")),
    )
    q = q.filter(IdRequirement.object_name == object_name) if object_name else q.filter(IdRequirement.object_name.is_(None))
    for r in q.all():
        add(r.title, r.doc_kind, r.section, r.norm_ref, "из ТЗ договора" if r.source == "tz" else "уже в комплекте", r.source)

    for rule in _RULES:
        pat = re.compile(rule["re"])
        n_rd, n_est = _count(pat, rd_names), _count(pat, est_names)
        if not (n_rd or n_est):
            continue
        parts = []
        if n_rd:
            parts.append(f"в РД {n_rd} поз.")
        if n_est:
            parts.append(f"в сметах {n_est} стр.")
        reason = f"{rule['label']}: " + ", ".join(parts)
        for title, kind, section, ref in rule["items"]:
            add(title, kind, section, ref, reason, "rule")

    for title, kind, section, ref, why in _ALWAYS:
        add(title, kind, section, ref, why, "norm")
    for sec in sections:
        for norm in NORM_SETS.get(sec, []):
            add(norm["title"], norm["doc_kind"], sec, norm["norm_ref"], f"норма для раздела {sec}", "norm")
    for norm in NORM_SETS["Общее"]:
        add(norm["title"], norm["doc_kind"], "Общее", norm["norm_ref"], "обязательный общий документ", "norm")

    items = sorted(proposals.values(), key=lambda p: (p["section"] or "я", p["title"]))
    return {
        "object_name": object_name,
        "sections": sections,
        "rd_items": len(rd_names),
        "estimate_lines": len(est_names),
        "items": items,
    }


def save_kit(db: Session, project_id: int, object_name: str | None, items: list[dict]) -> int:
    from app.models.executive_docs import IdRequirement

    q = db.query(IdRequirement).filter(
        IdRequirement.project_id == project_id, IdRequirement.source.in_(("tz", "norm", "kit")),
    )
    q = q.filter(IdRequirement.object_name == object_name) if object_name else q.filter(IdRequirement.object_name.is_(None))
    q.delete(synchronize_session=False)
    seen: set[str] = set()
    created = 0
    for it in items:
        title = (it.get("title") or "").strip()
        k = _norm_title(title)
        if not title or k in seen:
            continue
        seen.add(k)
        db.add(IdRequirement(
            project_id=project_id, object_name=object_name,
            section=it.get("section"), title=title[:2000],
            doc_kind=(it.get("doc_kind") or "other")[:30],
            required_if_applicable=False, source="kit",
            norm_ref=(it.get("norm_ref") or None), evidence_quote=(it.get("reason") or None),
        ))
        created += 1
    db.flush()
    return created

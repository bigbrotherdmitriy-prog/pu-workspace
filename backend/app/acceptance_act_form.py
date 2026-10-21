"""Fill the owner's own act form (Word) instead of a generated layout.

Owner 21.10.2026: «вот форма акта, замени». The form is the act for stage 1 of
Городец; every variable piece of it is replaced in place, so the fonts, table and
signature block stay exactly as in the owner's file.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from app.acceptance_act_document import ActData, _MONTHS_GEN, _money, rubles_in_words, vat_from_gross

FORM_PATH = Path(__file__).parent / "act_forms" / "customer_act.docx"

# What the form says for Городец, stage 1 — the pieces replaced per act.
_FORM_WORKS = ("Выполнение работ по модернизации системы бесперебойного питания ТЗ (технологической зоны) "
               "(Инв. № 1340401143) в Филиале ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Городец Нижегородской области")
_FORM_INTRO = ("выполнение работ по модернизации системы бесперебойного питания ТЗ (технологической зоны) "
               "в Филиале ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Городец Нижегородской области")
_FORM_REPRESENTATIVE = "Филиал ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Городец Нижегородской области"
_FORM_TOTAL = "22 000 000 (Двадцать два миллиона) рублей 00 копеек"
# The form keeps a non-breaking space after the first digit group in two places.
_FORM_VAT = "3\u00a0967 213 (Три миллиона девятьсот шестьдесят семь тысяч двести тринадцать) рублей 11 копеек"


@dataclass
class ObjectTexts:
    representative: str
    works: str


def object_texts(contract_text: str, object_name: Optional[str]) -> Optional[ObjectTexts]:
    """The representative and the works line for one object, as the contract's own act form words them."""
    if not object_name:
        return None
    text = re.sub(r"\s+", " ", contract_text or "")
    city = re.escape(object_name.strip().capitalize())
    place = rf"ЦОД в г\. {city}(?: [А-ЯЁ][а-яё]+ской области)?"
    match = re.search(
        rf"Наименование Представителя Заказчика:? (Филиал .{{0,80}}?{place}) "
        rf"(Выполнение работ .{{0,250}}?\(Инв\. № ?\d+\) в Филиале .{{0,80}}?{place})",
        text,
    )
    if not match:
        return None
    works = re.sub(r"системы системы", "системы", match.group(2)).rstrip(". ")
    return ObjectTexts(representative=match.group(1).rstrip(". "), works=works)


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return f"{n}-ый"
    return f"{n}-" + {1: "ый", 2: "ой", 3: "ий", 4: "ый", 5: "ый", 6: "ой", 7: "ой", 8: "ой", 9: "ый", 0: "ый"}[n % 10]


def _amount_phrase(amount: Decimal) -> str:
    """«22 000 000 (Двадцать два миллиона) рублей 00 копеек» — the form's wording."""
    words = rubles_in_words(amount)
    m = re.match(r"(.*) (рубл\w+) (\d\d) (копе\w+)$", words)
    whole = _money(amount).split(",")[0]
    if not m:
        return f"{whole} ({words})"
    return f"{whole} ({m.group(1)}) {m.group(2)} {m.group(3)} {m.group(4)}"


def _quantity(q: Decimal) -> str:
    return f"{q:.5f}".replace(".", ",")


def _replace_across_runs(xml: str, old: str, new: str) -> tuple[str, int]:
    """Replace text that Word split over several <w:t> runs of one paragraph."""
    count = 0
    out = []
    pos = 0
    for para in re.finditer(r"<w:p[ >].*?</w:p>", xml, re.S):
        body = para.group(0)
        nodes = list(re.finditer(r"(<w:t(?: [^>]*)?>)(.*?)(</w:t>)", body, re.S))
        texts = [n.group(2) for n in nodes]
        joined = "".join(texts)
        old_x = escape(old)
        if old_x not in joined:
            continue
        new_x = escape(new)
        search_from = 0
        while old_x in joined[search_from:]:
            start = joined.index(old_x, search_from)
            end = start + len(old_x)
            search_from = start + len(new_x)
            offset = 0
            for i, t in enumerate(texts):
                a, b = offset, offset + len(t)
                if b <= start or a >= end:
                    pass
                elif a <= start < b:
                    texts[i] = t[: start - a] + new_x + (t[end - a:] if end < b else "")
                else:
                    texts[i] = t[end - a:] if end < b else ""
                offset = b
            joined = "".join(texts)
            count += 1
        rebuilt = []
        last = 0
        for n, t in zip(nodes, texts):
            open_tag = n.group(1)
            if 'xml:space="preserve"' not in open_tag:
                open_tag = open_tag.replace("<w:t", '<w:t xml:space="preserve"', 1)
            rebuilt.append(body[last:n.start()] + open_tag + t + n.group(3))
            last = n.end()
        rebuilt.append(body[last:])
        out.append(xml[pos:para.start()] + "".join(rebuilt))
        pos = para.end()
    out.append(xml[pos:])
    return "".join(out), count


def render_act_from_form(data: ActData, texts: Optional[ObjectTexts], form: bytes | None = None) -> bytes:
    form = form if form is not None else FORM_PATH.read_bytes()
    src = zipfile.ZipFile(io.BytesIO(form))
    xml = src.read("word/document.xml").decode("utf-8")
    stage = _ordinal(int(data.stage_number))
    amount = Decimal(data.amount)
    vat = vat_from_gross(amount, data.vat_rate if data.vat_rate is not None else Decimal("22"))
    replacements: list[tuple[str, str]] = [
        ("выполненных работ № 1", f"выполненных работ № {data.number}"),
    ]
    if texts is not None:
        intro = texts.works[0].lower() + texts.works[1:]
        intro = re.sub(r" \(Инв\. № ?\d+\)", "", intro)
        replacements += [(_FORM_WORKS, texts.works), (_FORM_INTRO, intro), (_FORM_REPRESENTATIVE, texts.representative)]
    replacements += [
        ("1-ый этап", f"{stage} этап"),
        (_FORM_TOTAL, _amount_phrase(amount)),
        (_FORM_VAT, _amount_phrase(vat or Decimal("0"))),
        ("22 000 000,00", _money(amount)),
        ("22\u00a0000 000,00", _money(amount)),
        ("416 057 779,14", _money(Decimal(data.unit_price))),
        ("0,05288", _quantity(Decimal(data.quantity))),
    ]
    if data.act_date is not None:
        d = data.act_date
        replacements.append(("«____» ________________ 2026 г.", f"«{d.day:02d}» {_MONTHS_GEN[d.month - 1]} {d.year} г."))
    for old, new in replacements:
        xml, _ = _replace_across_runs(xml, old, new)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            payload = xml.encode("utf-8") if item.filename == "word/document.xml" else src.read(item.filename)
            dst.writestr(item, payload)
    return out.getvalue()

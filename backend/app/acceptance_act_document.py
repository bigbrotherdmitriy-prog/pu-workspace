from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from xml.sax.saxutils import escape

_MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

_UNITS_M = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
            "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
            "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_UNITS_F = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
            "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
            "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят",
         "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста",
             "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]


def _triple_words(n: int, feminine: bool) -> list[str]:
    words: list[str] = []
    h = n // 100
    t = (n // 10) % 10
    u = n % 10
    if h:
        words.append(_HUNDREDS[h])
    if t == 1:
        units = _UNITS_F if feminine else _UNITS_M
        words.append(units[10 + u])
    else:
        if t:
            words.append(_TENS[t])
        if u:
            units = _UNITS_F if feminine else _UNITS_M
            words.append(units[u])
    return words


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n = n % 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _capitalize_first(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def rubles_in_words(amount: Decimal) -> str:
    amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = amount < 0
    if negative:
        amount = -amount
    rub = int(amount)
    kop = int((amount - rub) * 100)

    words: list[str] = []
    if rub == 0:
        words.append("ноль")
    else:
        billions = rub // 1_000_000_000
        millions = (rub // 1_000_000) % 1000
        thousands = (rub // 1000) % 1000
        units = rub % 1000

        if billions:
            words.extend(_triple_words(billions, feminine=False))
            words.append(_plural(billions, "миллиард", "миллиарда", "миллиардов"))
        if millions:
            words.extend(_triple_words(millions, feminine=False))
            words.append(_plural(millions, "миллион", "миллиона", "миллионов"))
        if thousands:
            words.extend(_triple_words(thousands, feminine=True))
            words.append(_plural(thousands, "тысяча", "тысячи", "тысяч"))
        if units:
            words.extend(_triple_words(units, feminine=False))

    rub_word = _plural(rub, "рубль", "рубля", "рублей")
    kop_word = _plural(kop, "копейка", "копейки", "копеек")

    text = " ".join(words + [rub_word, f"{kop:02d}", kop_word])
    if negative:
        text = "минус " + text
    return _capitalize_first(text)


def vat_from_gross(amount: Decimal, rate: Optional[Decimal]) -> Optional[Decimal]:
    if rate is None:
        return None
    amount = Decimal(amount)
    rate = Decimal(rate)
    vat = amount * rate / (Decimal(100) + rate)
    return vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def stage_quantity(amount: Decimal, object_total: Decimal, places: int = 5) -> Decimal:
    """Stage share of the object price (усл. ед.); 5 places is what customers accept in acts."""
    amount = Decimal(amount)
    object_total = Decimal(object_total)
    if object_total <= 0:
        return Decimal("1")
    q = amount / object_total
    return q.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


@dataclass
class ActParty:
    name: str
    role: str
    signer_position: str
    signer_name: str
    basis: str = "Устава"


@dataclass
class ActData:
    number: str
    act_date: Optional[date]
    city: Optional[str]
    contract_number: str
    contract_date: Optional[date]
    customer: ActParty
    contractor: ActParty
    object_name: Optional[str]
    stage_number: int
    stage_title: str
    okpd2: Optional[str]
    unit: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    vat_rate: Optional[Decimal]
    customer_representative: Optional[str] = None


def _money(value: Decimal) -> str:
    value = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = value < 0
    if negative:
        value = -value
    int_part = int(value)
    frac = int((value - int_part) * 100)
    s = f"{int_part:,}".replace(",", " ")
    return ("-" if negative else "") + f"{s},{frac:02d}"


def _date_words(d: Optional[date]) -> str:
    if d is None:
        return "«__» ________ 20__ года"
    return f"«{d.day:02d}» {_MONTHS_GEN[d.month - 1]} {d.year} года"


def _date_short(d: Optional[date]) -> str:
    if d is None:
        return "«__» ________ 20__"
    return f"«{d.day:02d}» {_MONTHS_GEN[d.month - 1]} {d.year}"


def _fmt_quantity(q: Decimal) -> str:
    q = Decimal(q).normalize()
    s = format(q, "f")
    if "." in s:
        int_part, frac = s.split(".", 1)
    else:
        int_part, frac = s, ""
    frac = frac.rstrip("0")
    while len(frac) < 5:
        frac += "0"
    return f"{int_part},{frac}"


def _vat_label(rate: Optional[Decimal]) -> str:
    if rate is None:
        return "без НДС"
    r = Decimal(rate).normalize()
    s = format(r, "f")
    return f"в т.ч. НДС {s}%"


def act_text_lines(data: ActData) -> dict:
    title = f"Акт сдачи-приемки выполненных работ № {data.number}"
    subtitle = f"по Договору № {data.contract_number} от {_date_words(data.contract_date)}"

    city_str = f"г. {data.city}" if data.city else None
    place_date = {
        "left": city_str,
        "right": _date_short(data.act_date),
    }

    cust = data.customer
    contr = data.contractor
    cust_rep = data.customer_representative
    if cust_rep:
        cust_in_person = f"{cust.signer_position} {cust.signer_name}"
        cust_face_line = (
            f"Мы, нижеподписавшиеся, {cust.name}, именуемое в дальнейшем Заказчик, "
            f"в лице {cust_in_person}, действующего на основании {cust.basis}, с одной стороны, "
            f"и {contr.name}, именуемое в дальнейшем Подрядчик, "
            f"в лице {contr.signer_position} {contr.signer_name}, действующего на основании {contr.basis}, "
            f"с другой стороны, составили настоящий Акт о том, что в соответствии с Договором "
            f"№ {data.contract_number} от {_date_words(data.contract_date)} "
            f"Подрядчик выполнил, а Заказчик принял работы:"
        )
    else:
        cust_face_line = (
            f"Мы, нижеподписавшиеся, {cust.name}, именуемое в дальнейшем Заказчик, "
            f"в лице {cust.signer_position} {cust.signer_name}, действующего на основании {cust.basis}, с одной стороны, "
            f"и {contr.name}, именуемое в дальнейшем Подрядчик, "
            f"в лице {contr.signer_position} {contr.signer_name}, действующего на основании {contr.basis}, "
            f"с другой стороны, составили настоящий Акт о том, что в соответствии с Договором "
            f"№ {data.contract_number} от {_date_words(data.contract_date)} "
            f"Подрядчик выполнил, а Заказчик принял работы:"
        )

    if data.object_name:
        works_line = f"{data.stage_title} ({data.object_name}, {data.stage_number}-й этап)."
    else:
        works_line = f"{data.stage_title} ({data.stage_number}-й этап)."

    parties = f"{cust_face_line} {works_line}"

    if data.vat_rate is None:
        price_hdr = "Цена за ед., без НДС"
        total_hdr = "Общая стоимость, без НДС"
        vat_str = "НДС не облагается"
    else:
        rate_s = format(Decimal(data.vat_rate).normalize(), "f")
        price_hdr = f"Цена за ед., в т.ч. НДС {rate_s}%"
        total_hdr = f"Общая стоимость, в т.ч. НДС {rate_s}%"
        vat = vat_from_gross(data.amount, data.vat_rate)
        vat_str = (
            f"в т.ч. НДС {rate_s}% — {_money(vat)} ({rubles_in_words(vat)})"
        )

    table_header = [
        "Наименование работ",
        "ОКПД2",
        "Ед. измерения",
        "Кол-во",
        price_hdr,
        total_hdr,
    ]
    table_row = [
        data.stage_title,
        data.okpd2 or "",
        data.unit,
        _fmt_quantity(data.quantity),
        _money(data.unit_price),
        _money(data.amount),
    ]
    total = f"Итого: {_money(data.amount)}"

    amount_words = (
        f"Общая цена выполненных работ ({data.stage_number}-й этап) составила "
        f"{_money(data.amount)} ({rubles_in_words(data.amount)}), {vat_str}."
    )

    closing = (
        "Работы выполнены в полном объёме и в установленные сроки. "
        "Заказчик претензий по объёму, качеству и срокам выполнения работ не имеет."
    )

    signatures = [
        ("Заказчик", cust.name, cust.signer_position, cust.signer_name),
        ("Подрядчик", contr.name, contr.signer_position, contr.signer_name),
    ]

    return {
        "title": title,
        "subtitle": subtitle,
        "place_date": place_date,
        "parties": parties,
        "table_header": table_header,
        "table_row": table_row,
        "total": total,
        "amount_words": amount_words,
        "closing": closing,
        "signatures": signatures,
    }


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
</Relationships>"""

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _xml_escape(text: str) -> str:
    return escape(text)


def _run(text: str, bold: bool = False, size: int = 24) -> str:
    rpr = (
        '<w:rPr>'
        '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
        + ("<w:b/>" if bold else "")
        + f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        '</w:rPr>'
    )
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'


def _para(text: str = "", bold: bool = False, align: Optional[str] = None) -> str:
    ppr_inner = ""
    if align:
        ppr_inner += f'<w:jc w:val="{align}"/>'
    ppr = f"<w:pPr>{ppr_inner}</w:pPr>" if ppr_inner else ""
    return f"<w:p>{ppr}{_run(text, bold=bold) if text else ''}</w:p>"


def _cell(text: str, bold: bool = False, width: Optional[int] = None) -> str:
    tcpr = f'<w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>' if width else ""
    return f"<w:tc>{tcpr}{_para(text, bold=bold)}</w:tc>"


def _table(rows: list[list[str]], border: bool = True, header_bold: bool = True) -> str:
    tblpr = ""
    if border:
        tblpr = (
            "<w:tblPr>"
            "<w:tblBorders>"
            '<w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            "</w:tblBorders>"
            "</w:tblPr>"
        )
    else:
        tblpr = (
            "<w:tblPr>"
            "<w:tblBorders>"
            '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            "</w:tblBorders>"
            "</w:tblPr>"
        )
    body = ""
    for idx, row in enumerate(rows):
        cells = "".join(_cell(c, bold=(header_bold and idx == 0)) for c in row)
        body += f"<w:tr>{cells}</w:tr>"
    return f"<w:tbl>{tblpr}{body}</w:tbl>"


def render_act_docx(data: ActData) -> bytes:
    lines = act_text_lines(data)

    paragraphs: list[str] = []
    paragraphs.append(_para(lines["title"], bold=True, align="center"))
    paragraphs.append(_para(lines["subtitle"], align="center"))
    paragraphs.append(_para(""))

    pd = lines["place_date"]
    left = pd["left"] or ""
    right = pd["right"]
    if left:
        paragraphs.append(_table([[left, right]], border=False, header_bold=False))
    else:
        paragraphs.append(_para(right, align="right"))
    paragraphs.append(_para(""))

    paragraphs.append(_para(lines["parties"]))
    paragraphs.append(_para(""))

    th = lines["table_header"]
    tr = lines["table_row"]
    total_row = ["", "", "", "", "Итого:", _money(data.amount)]
    paragraphs.append(_table([th, tr, total_row]))

    paragraphs.append(_para(""))
    paragraphs.append(_para(lines["amount_words"]))
    paragraphs.append(_para(""))
    paragraphs.append(_para(lines["closing"]))
    paragraphs.append(_para(""))

    sig_rows = [
        ["Заказчик:", "Подрядчик:"],
        [data.customer.name, data.contractor.name],
        [data.customer.signer_position, data.contractor.signer_position],
        [
            f"_____________ / {data.customer.signer_name}",
            f"_____________ / {data.contractor.signer_name}",
        ],
        ["М.П.", "М.П."],
    ]
    paragraphs.append(_table(sig_rows, border=False, header_bold=False))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}">'
        "<w:body>"
        + "".join(paragraphs)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" '
        'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
    return buf.getvalue()

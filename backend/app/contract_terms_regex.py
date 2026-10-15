from __future__ import annotations

from datetime import date
from decimal import Decimal
import re


_DATE_PATTERN = r"(\d{1,2})[./](\d{1,2})[./](20\d{2})"


def _date_value(day: str, month: str, year: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


_RU_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def _spaced_word(word: str) -> str:
    # OCR splits words with stray spaces («Ок ончание»): allow \s* between letters.
    return r"\s*".join(re.escape(letter) for letter in word)


_WORDY_DATE = r"«?\s*(\d{1,2})\s*»?\s+([а-яё]+)\s+(20\s*\d\s*\d)"


def _wordy_date_value(day: str, month_word: str, year: str) -> date | None:
    month_word = month_word.casefold()
    month = next((number for stem, number in sorted(_RU_MONTHS.items(), key=lambda item: -len(item[0]))
                  if month_word.startswith(stem)), None)
    if month is None:
        return None
    return _date_value(day, str(month), re.sub(r"\s+", "", year))


def _work_boundary_date(text: str, word: str) -> date | None:
    """«Начало Работ: «27» марта 2026 года» / «Окончание выполнения работ: 31.12.2026»."""
    pattern = rf"{_spaced_word(word)}\s*(?:{_spaced_word('выполнения')}\s*)?{_spaced_word('работ')}\s*[:\-–]?\s*"
    match = re.search(pattern + _WORDY_DATE, text, re.IGNORECASE)
    if match:
        return _wordy_date_value(*match.groups())
    match = re.search(pattern + _DATE_PATTERN, text, re.IGNORECASE)
    return _date_value(*match.groups()) if match else None


def _decimal_value(raw: str) -> Decimal:
    # «300000-00» / «180327-87» is rubles-kopecks written with a dash.
    return Decimal(re.sub(r"-(\d{2})$", r".\1", re.sub(r"\s+", "", raw)).replace(",", "."))


def extract_contract_financial_terms(content: str) -> dict:
    """Extract explicit contract price, advance, retention, VAT state, dates
    and warranty with source evidence (ADR V6-10, step 8б: "предложение суммы,
    дат и НДС ... со ссылкой на источник"). Regex over explicit phrasing only --
    this is not NLP; an absent match leaves the field None, never a guess."""
    result: dict[str, Decimal | str | date | None] = {
        "amount": None, "advance_amount": None, "advance_percent": None,
        "retention_percent": None, "amount_evidence": None,
        "advance_evidence": None, "retention_evidence": None,
        "signed_at": None, "signed_at_evidence": None,
        "vat_mode": None, "vat_rate": None, "vat_evidence": None,
        "performed_from": None, "performed_to": None, "performed_evidence": None,
        "warranty_until": None, "warranty_evidence": None,
    }
    money_pattern = (
        r"(?<!\d)(\d[\d\s]{2,}(?:[.,]\d{1,2}|-\d{2}(?!\d))?)"
        r"(?:\s*=?\s*\([^)]{0,200}\))?\s*"
        r"(?:руб(?:\.|лей|ля)?|₽)"
    )
    percent_pattern = r"(?<!\d)(\d{1,3}(?:[.,]\d{1,2})?)\s*%"
    amount_keywords = re.compile(
        r"цена\s+(?:настоящего\s+)?договора|цена\s+работ|общая\s+цена|"
        r"стоимость\s+(?:работ|услуг|договора)|общая\s+стоимость|составляет|стоимость\s+всех|в\s+сумме",
        re.IGNORECASE,
    )
    vat_keywords = re.compile(
        r"(?:в\s*т\.?\s*ч\.?|в\s+том\s+числе|включая\s+ндс|кроме\s+ндс|плюс\s+ндс|"
        r"ндс\s+составляет|ндс\s+в\s+размере)",
        re.IGNORECASE,
    )
    vat_percent_after_keyword = re.compile(
        r"(?:в\s*т\.?\s*ч\.?|в\s+том\s+числе|включая\s+ндс|кроме\s+ндс|плюс\s+ндс|"
        r"ндс\s+составляет|ндс\s+в\s+размере)\s*[:\-]?\s*НДС\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*%",
        re.IGNORECASE,
    )
    vat_percent_generic = re.compile(
        r"НДС\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*%",
        re.IGNORECASE,
    )
    vat_none = re.compile(
        r"(?:не\s+облагается|без\s+ндс|ндс\s+не\s+предусмотрен|"
        r"не\s+признаётся\s+плательщиком\s+ндс)",
        re.IGNORECASE,
    )
    retention_marker = re.compile(r"удержан|удержива", re.IGNORECASE)
    retention_generic = re.compile(r"гарантийн[а-яё]*\s+удерж", re.IGNORECASE)
    date_pattern = re.compile(_DATE_PATTERN)
    period_pattern = re.compile(
        rf"(?:срок|период)\s+(?:выполнения|исполнения|оказания\s+услуг|действия)[^.\n]*?"
        rf"с\s+{_DATE_PATTERN}\s+(?:по|до)\s+{_DATE_PATTERN}",
        re.IGNORECASE,
    )
    deadline_pattern = re.compile(
        rf"(?:срок[а-яё]*\s+(?:выполнения|исполнения|оказания\s+услуг|поставки|работ)|"
        rf"не\s+позднее|до)\s*[«\"]?\s*{_DATE_PATTERN}"
        rf"|(?:срок[а-яё]*\s+(?:выполнения|исполнения|оказания\s+услуг|поставки|работ))\s*[^.\n]*?"
        rf"не\s+позднее\s*[«\"]?\s*{_DATE_PATTERN}",
        re.IGNORECASE,
    )
    month_names = {
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
        "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    }
    text_deadline_pattern = re.compile(
        rf"(?:не\s+позднее|до)\s*[«\"]?\s*(\d{{1,2}})[»\"]?\s+"
        rf"({'|'.join(month_names)})\s+(\d{{4}})",
        re.IGNORECASE,
    )
    current_year = date.today().year
    min_signed_year = current_year - 5
    max_signed_year = current_year + 2
    lines = [re.sub(r"\s+", " ", line).strip() for line in content.splitlines() if line.strip()]
    for line in lines:
        # Scans/DOCX split numbers with stray spaces («НДС 2 2 %», «20 2 6»); digits
        # separated only by spaces belong to one number.
        line = re.sub(r"(?<=\d)\s+(?=\d)", "", line)
        lowered = line.casefold()
        money_matches = list(re.finditer(money_pattern, line, re.IGNORECASE))
        # The contract price is the largest amount on its line (VAT and advances are smaller).
        money = max(money_matches, key=lambda item: _decimal_value(item.group(1)) or 0, default=None)
        percent = re.search(percent_pattern, line)
        if result["amount"] is None and money and amount_keywords.search(lowered):
            result["amount"] = _decimal_value(money.group(1))
            result["amount_evidence"] = line[:500]
        if "аванс" in lowered:
            # Only figures that follow the word «аванс» closely belong to the advance;
            # the contract price and VAT often share the same paragraph.
            window = " ".join(match.group(0) for match in re.finditer(r"аванс\w*[^.;]{0,160}", line, re.IGNORECASE))
            advance_money = re.search(money_pattern, window, re.IGNORECASE)
            advance_percent = re.search(percent_pattern, window)
            if advance_percent and re.search(r"ндс\s*$", window[:advance_percent.start()].casefold()):
                advance_percent = None
            if result["advance_amount"] is None and advance_money:
                result["advance_amount"] = _decimal_value(advance_money.group(1))
            if result["advance_percent"] is None and advance_percent:
                result["advance_percent"] = _decimal_value(advance_percent.group(1))
            if advance_money or advance_percent:
                result["advance_evidence"] = line[:500]
        if result["retention_percent"] is None:
            marker = retention_marker.search(line) or retention_generic.search(line)
            if marker:
                tail = line[marker.start():marker.start() + 60 + len(marker.group(0))]
                vat_near = re.search(r"НДС", tail, re.IGNORECASE)
                percent_in_tail = re.search(percent_pattern, tail)
                if percent_in_tail and not vat_near:
                    result["retention_percent"] = _decimal_value(percent_in_tail.group(1))
                    result["retention_evidence"] = line[:500]
        if result["signed_at"] is None and re.search(r"подписан|дата\s+подписания", lowered):
            match = date_pattern.search(line)
            if match and (value := _date_value(*match.groups())) is not None:
                if min_signed_year <= value.year <= max_signed_year:
                    result["signed_at"] = value
                    result["signed_at_evidence"] = line[:500]
        if result["vat_mode"] is None and "ндс" in lowered:
            if vat_none.search(line):
                result["vat_mode"] = "none"
                result["vat_evidence"] = line[:500]
            else:
                vat_rate_match = vat_percent_after_keyword.search(line)
                if vat_rate_match is None and vat_keywords.search(line):
                    vat_rate_match = vat_percent_generic.search(line)
                if vat_rate_match is None:
                    vat_rate_match = vat_percent_generic.search(line)
                if vat_rate_match is not None:
                    result["vat_mode"] = "rate"
                    result["vat_rate"] = _decimal_value(vat_rate_match.group(1))
                    result["vat_evidence"] = line[:500]
        if result["performed_from"] is None or result["performed_to"] is None:
            match = period_pattern.search(line)
            if match:
                groups = match.groups()
                start, end = _date_value(*groups[0:3]), _date_value(*groups[3:6])
                if start is not None and end is not None:
                    result["performed_from"], result["performed_to"] = start, end
                    result["performed_evidence"] = line[:500]
        if result["performed_to"] is None and re.search(
            r"срок[а-яё]*\s+(?:выполнения|исполнения|оказания\s+услуг|поставки|работ)"
            r"|не\s+позднее",
            lowered,
        ):
            numeric = deadline_pattern.search(line)
            value: date | None = None
            if numeric is not None:
                groups = numeric.groups()
                if all(groups[:3]):
                    value = _date_value(*groups[0:3])
            if value is None:
                textual = text_deadline_pattern.search(line)
                if textual:
                    day, month_name, year = textual.groups()
                    month_number = month_names.get(month_name.casefold())
                    if month_number is not None:
                        try:
                            value = date(int(year), month_number, int(day))
                        except ValueError:
                            value = None
            if value is not None:
                result["performed_to"] = value
                if result["performed_evidence"] is None:
                    result["performed_evidence"] = line[:500]
        if result["warranty_until"] is None and re.search(r"гарантийн.*срок|гарант(?:ия|ийные\s+обязательства)", lowered):
            match = re.search(rf"(?:до|по)\s+{_DATE_PATTERN}", line, re.IGNORECASE)
            if match and (value := _date_value(*match.groups())) is not None:
                result["warranty_until"] = value
                result["warranty_evidence"] = line[:500]
    flat = re.sub(r"\s+", " ", content)
    if result["performed_from"] is None and (start := _work_boundary_date(flat, "начало")) is not None:
        result["performed_from"] = start
        result["performed_evidence"] = result["performed_evidence"] or "Начало работ: " + start.isoformat()
    if result["performed_to"] is None and (finish := _work_boundary_date(flat, "окончание")) is not None:
        result["performed_to"] = finish
        result["performed_evidence"] = result["performed_evidence"] or "Окончание работ: " + finish.isoformat()
    if result["advance_amount"] is None and result["advance_percent"] is not None and result["amount"] is not None:
        result["advance_amount"] = (result["amount"] * result["advance_percent"] / Decimal("100")).quantize(Decimal("0.01"))
    return result

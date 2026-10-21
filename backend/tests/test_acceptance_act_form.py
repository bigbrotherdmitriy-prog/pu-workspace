import io
import re
import zipfile
from datetime import date
from decimal import Decimal

from app.acceptance_act_document import ActData, ActParty
from app.acceptance_act_form import object_texts, render_act_from_form

CONTRACT = (
    "Наименование Представителя Заказчика: Филиал ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Дубна "
    "Выполнение работ по модернизации системы бесперебойного электропитания ТЗ (технологической зоны) "
    "(Инв. № 1340401048) в Филиале ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Дубна. 43.21.10.290 Усл.ед . 1 Итого: "
    "Наименование Представителя Заказчика Филиал ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Городец Нижегородской области "
    "Выполнение работ по модернизации системы системы бесперебойного питания ТЗ (технологической зоны) "
    "(Инв. № 1340401143) в Филиале ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Городец Нижегородской области 43.21.10.290"
)


def _text(docx: bytes) -> str:
    xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode()
    return re.sub(r"<[^>]+>", "", xml)


def _data(**kw):
    party = ActParty(name="x", role="customer", signer_position="", signer_name="")
    values = dict(number="14", act_date=None, city=None, contract_number="Б-УЗП/130-02-2026", contract_date=None,
                  customer=party, contractor=party, object_name="ДУБНА", stage_number=5, stage_title="",
                  okpd2=None, unit="Усл. ед.", quantity=Decimal("0.21597"), unit_price=Decimal("261692220.86"),
                  amount=Decimal("56516666.34"), vat_rate=Decimal("22"))
    values.update(kw)
    return ActData(**values)


def test_object_texts_follow_the_contract_wording():
    dubna = object_texts(CONTRACT, "ДУБНА")
    assert dubna.works.endswith("(Инв. № 1340401048) в Филиале ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Дубна")
    assert dubna.representative == "Филиал ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Дубна"
    gorodets = object_texts(CONTRACT, "ГОРОДЕЦ")
    assert "системы системы" not in gorodets.works
    assert gorodets.works.endswith("г. Городец Нижегородской области")


def test_owner_form_is_filled_for_another_object_and_stage():
    text = _text(render_act_from_form(_data(), object_texts(CONTRACT, "ДУБНА")))
    assert "Акт сдачи-приемки выполненных работ № 14" in text
    assert "5-ый этап" in text and "1-ый этап" not in text
    assert "Инв. № 1340401048" in text and "1340401143" not in text
    assert "Городец" not in text
    assert "56 516 666,34" in text and "22 000 000,00" not in text
    assert "56 516 666 (Пятьдесят шесть миллионов пятьсот шестнадцать тысяч шестьсот шестьдесят шесть) рублей 34 копейки" in text
    assert "261 692 220,86" in text and "0,21597" in text
    assert "Комаров" in text
    assert "22\u00a0000" not in text and "3\u00a0967" not in text
    assert "НДС 22% - 10 191 530 (Десять миллионов сто девяносто одна тысяча пятьсот тридцать) рублей 00 копеек" in text


def test_signed_act_gets_its_date():
    text = _text(render_act_from_form(_data(act_date=date(2026, 10, 21)), object_texts(CONTRACT, "ДУБНА")))
    assert "«21» октября 2026 г." in text

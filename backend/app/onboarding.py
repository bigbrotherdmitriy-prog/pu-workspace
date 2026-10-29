"""Помощник для новичков: какие шаги по проекту уже сделаны и куда идти дальше."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.equipment import EquipmentUnit
from app.models.execution_finance import CashFlowEntry, ScheduleItem
from app.models.executive_docs import IdSource
from app.models.organization_contract import Contract
from app.models.purchase import PurchaseItem
from app.models.rd import RdSpecItem, RdVolume
from app.models.work_log import WorkLogEntry


def _count(db: Session, model, *conditions) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


def steps(db: Session, project_id: int) -> dict:
    n = {
        "contracts": _count(db, Contract, Contract.project_id == project_id),
        "documents": _count(db, Document, Document.project_id == project_id),
        "schedule": _count(db, ScheduleItem, ScheduleItem.project_id == project_id),
        "dds": _count(db, CashFlowEntry, CashFlowEntry.project_id == project_id),
        "rd_volumes": _count(db, RdVolume, RdVolume.project_id == project_id),
        "rd_confirmed": _count(db, RdSpecItem, RdSpecItem.project_id == project_id, RdSpecItem.status == "confirmed"),
        "purchase": _count(db, PurchaseItem, PurchaseItem.project_id == project_id),
        "equipment": _count(db, EquipmentUnit, EquipmentUnit.project_id == project_id),
        "id_sources": _count(db, IdSource, IdSource.project_id == project_id),
        "work_log": _count(db, WorkLogEntry, WorkLogEntry.project_id == project_id),
    }
    items = [
        {"key": "contract", "title": "Загрузите договор", "done": n["contracts"] > 0, "screen": "Договоры",
         "hint": "Система сама найдёт стороны, суммы и сроки, вам останется проверить."},
        {"key": "documents", "title": "Добавьте документы проекта", "done": n["documents"] > 0, "screen": "Документы",
         "hint": "Загрузите файлы или подключите папку на Диске: «Запуск проекта» разберёт её."},
        {"key": "schedule", "title": "Загрузите график работ (ГПР)", "done": n["schedule"] > 0, "screen": "ГПР и ДДС",
         "hint": "ГПР задаёт этапы и сроки, по нему считается ход проекта."},
        {"key": "dds", "title": "Загрузите плановый ДДС", "done": n["dds"] > 0, "screen": "ГПР и ДДС",
         "hint": "Плановый ДДС не меняется: счета и оплаты привязываются к его строкам."},
        {"key": "rd", "title": "Загрузите тома рабочей документации", "done": n["rd_volumes"] > 0, "screen": "Рабочая документация",
         "hint": "Укажите ссылку на папку с РД: система найдёт спецификации."},
        {"key": "rd_confirm", "title": "Проверьте и подтвердите позиции спецификации", "done": n["rd_confirmed"] > 0, "screen": "Рабочая документация",
         "hint": "Подтверждённые позиции попадают в закупку и оборудование."},
        {"key": "purchase", "title": "Соберите закупку", "done": n["purchase"] > 0, "screen": "Закупка",
         "hint": "Сводная спецификация, счета поставщиков и отчёт «нужно / заказано / оплачено»."},
        {"key": "equipment", "title": "Создайте реестр оборудования", "done": n["equipment"] > 0, "screen": "Оборудование",
         "hint": "Заводские номера, гарантия, даты обслуживания."},
        {"key": "id", "title": "Подключите папку исполнительной документации", "done": n["id_sources"] > 0, "screen": "Исполнительная документация",
         "hint": "Система покажет готовность комплекта и чего не хватает."},
        {"key": "work_log", "title": "Начните вести журнал работ", "done": n["work_log"] > 0, "screen": "Журнал работ",
         "hint": "Прораб вносит факт с телефона в день выполнения работ."},
    ]
    done = sum(1 for i in items if i["done"])
    next_item = next((i for i in items if not i["done"]), None)
    return {
        "items": items,
        "done": done,
        "total": len(items),
        "percent": round(done * 100 / len(items)),
        "next": next_item["key"] if next_item else None,
    }

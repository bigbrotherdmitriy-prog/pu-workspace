from __future__ import annotations

import io
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.rd import RdSheet, RdSpecItem, RdVolume
from app.rd_ingest import find_spec_pages, spec_pages_label, spec_text, split_pages
from app.rd_sheets import build_register
from app.specification import specification_from_text


logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^[A-ZА-ЯЁ]{2,6}$")

_OCR_CHUNK = 15


def _ocr_blank_pages(pdf_bytes: bytes, pages: list[str], sources: list[str]) -> tuple[list[str], int]:
    blank = [i for i, s in enumerate(sources) if s == "none"]
    if not blank:
        return pages, 0
    try:
        limit = int(os.getenv("RD_OCR_MAX_PAGES", "60"))
    except (TypeError, ValueError):
        limit = 60
    if limit < 0:
        limit = 0
    targets = blank[:limit]
    if not targets:
        return pages, 0
    from app.organizer_engine.content import _ocr_pdf_pages

    page_numbers = {i + 1 for i in targets}
    ocr_done = 0
    ordered = sorted(page_numbers)
    chunks = [ordered[i:i + _OCR_CHUNK] for i in range(0, len(ordered), _OCR_CHUNK)]
    for chunk in chunks:
        try:
            result = _ocr_pdf_pages(pdf_bytes, set(chunk))
        except Exception as exc:  # noqa: BLE001 - OCR не должен валить том
            logger.warning("rd parse ocr failed for volume pages %s: %s", chunk, exc)
            continue
        for n, extraction in (result or {}).items():
            idx = n - 1
            if idx < 0 or idx >= len(pages):
                continue
            text = getattr(extraction, "text", "") or ""
            if text.strip():
                pages[idx] = text
                sources[idx] = "ocr"
                ocr_done += 1
    return pages, ocr_done


def _strip_ext(name: str) -> str:
    if name.lower().endswith(".pdf"):
        return name[:-4]
    return name


def guess_meta(file_name: str) -> dict:
    stem = _strip_ext(file_name or "")
    section: str | None = None
    code = stem
    if "-" in stem:
        head, _, tail = stem.rpartition("-")
        if _SECTION_RE.match(tail) and head:
            section = tail
            code = head
    return {"section": section, "code": code or None, "object_name": None}


def _list_folder_pdfs(svc, folder_id: str) -> list[dict]:
    files: list[dict] = []
    page_token: str | None = None
    while True:
        kwargs: dict = {
            "q": f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
            "fields": "nextPageToken,files(id,name,size,mimeType)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "pageSize": 1000,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.files().list(**kwargs).execute()
        files.extend(resp.get("files") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def _download(svc, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    dl = MediaIoBaseDownload(buf, req, chunksize=8 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        text = str(value).strip().replace(",", ".")
        if not text:
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def import_folder(db: Session, project_id: int, folder_id: str) -> dict:
    from app.api.google_drive import drive_service_for_project

    svc = drive_service_for_project(project_id, db)
    files = _list_folder_pdfs(svc, folder_id)

    added = 0
    existing = 0
    ids: list[int] = []
    for f in files:
        ext_id = f.get("id")
        name = f.get("name") or ""
        if not ext_id:
            continue
        found = db.execute(
            select(RdVolume).where(
                RdVolume.project_id == project_id,
                RdVolume.provider == "google_drive",
                RdVolume.external_id == ext_id,
            )
        ).scalar_one_or_none()
        if found is not None:
            existing += 1
            ids.append(found.id)
            continue
        meta = guess_meta(name)
        size = f.get("size")
        try:
            size_bytes = int(size) if size is not None else None
        except (TypeError, ValueError):
            size_bytes = None
        vol = RdVolume(
            project_id=project_id,
            object_name=meta["object_name"],
            section=meta["section"],
            code=meta["code"],
            title=_strip_ext(name),
            file_name=name,
            provider="google_drive",
            external_id=ext_id,
            size_bytes=size_bytes,
            status="new",
        )
        db.add(vol)
        db.flush()
        added += 1
        ids.append(vol.id)

    db.commit()
    return {"added": added, "existing": existing, "volumes": ids}


def parse_volume(db: Session, volume_id: int) -> dict:
    from app.api.google_drive import drive_service_for_project

    vol = db.get(RdVolume, volume_id)
    if vol is None:
        return {"items": 0, "spec_pages": ""}

    try:
        vol.status = "parsing"
        vol.error_message = None
        db.commit()

        svc = drive_service_for_project(vol.project_id, db)
        pdf_bytes = _download(svc, vol.external_id or "")
        pages = split_pages(pdf_bytes)
        sources = ["native" if len(t.strip()) >= 20 else "none" for t in pages]
        pages, ocr_done = _ocr_blank_pages(pdf_bytes, pages, sources)
        vol.page_count = len(pages)

        nums = find_spec_pages(pages)
        label = spec_pages_label(nums)
        vol.spec_pages = label

        register = build_register(pages, nums, sources)
        db.execute(select(RdSheet).where(RdSheet.volume_id == vol.id))
        old_sheets = db.execute(
            select(RdSheet).where(RdSheet.volume_id == vol.id)
        ).scalars().all()
        for sheet in old_sheets:
            db.delete(sheet)
        db.flush()
        for row in register:
            db.add(RdSheet(volume_id=vol.id, project_id=vol.project_id, **row))
        db.flush()

        if not nums:
            vol.status = "parsed"
            db.commit()
            return {
                "items": 0,
                "spec_pages": "",
                "sheets": len(register),
                "ocr_pages": ocr_done,
            }

        parsed_items: list[dict] = []
        page_errors: list[str] = []
        for num in nums:
            try:
                items, _method = specification_from_text(spec_text(pages, [num]))
            except Exception as exc:  # noqa: BLE001 - одна страница не валит том
                page_errors.append(f"стр.{num}: {exc}")
                continue
            for it in items or []:
                name = it.get("name")
                if not name:
                    continue
                parsed_items.append({
                    "page": num,
                    "pos": it.get("pos"),
                    "name": name,
                    "type_mark": it.get("type"),
                    "unit": it.get("unit"),
                    "qty": _to_decimal(it.get("qty")),
                    "note": it.get("note"),
                })

        db.execute(
            select(RdSpecItem).where(
                RdSpecItem.volume_id == vol.id,
                RdSpecItem.status == "proposed",
            )
        )
        # удаляем только proposed
        old = db.execute(
            select(RdSpecItem).where(
                RdSpecItem.volume_id == vol.id,
                RdSpecItem.status == "proposed",
            )
        ).scalars().all()
        for item in old:
            db.delete(item)
        db.flush()
        # Подтверждённые позиции со страниц, которые больше не считаются спецификацией
        # (например, ведомость демонтажных работ), отклоняем — в закупку они не должны попадать.
        spec_set = set(nums)
        stale = db.execute(
            select(RdSpecItem).where(
                RdSpecItem.volume_id == vol.id,
                RdSpecItem.status == "confirmed",
            )
        ).scalars().all()
        for item in stale:
            if item.source_page is not None and item.source_page not in spec_set:
                item.status = "rejected"
        db.flush()

        def _key(pos, name, type_mark, unit, qty, page):
            return (
                (pos or "").strip(), (name or "").strip().lower(), (type_mark or "").strip().lower(),
                (unit or "").strip().lower(), f"{Decimal(qty).normalize():f}" if qty is not None else "", page,
            )

        kept = {
            _key(i.pos, i.name, i.type_mark, i.unit, i.qty, i.source_page)
            for i in db.execute(
                select(RdSpecItem).where(RdSpecItem.volume_id == vol.id)
            ).scalars().all()
        }
        fresh: list[dict] = []
        for data in parsed_items:
            k = _key(data["pos"], data["name"], data["type_mark"], data["unit"], data["qty"], data["page"])
            if k in kept:
                continue  # уже есть (подтверждена или отклонена) — копию не создаём
            kept.add(k)
            fresh.append(data)
        parsed_items = fresh

        for idx, data in enumerate(parsed_items, start=1):
            db.add(RdSpecItem(
                volume_id=vol.id,
                project_id=vol.project_id,
                sort_order=idx,
                pos=data["pos"],
                name=data["name"],
                type_mark=data["type_mark"],
                unit=data["unit"],
                qty=data["qty"],
                note=data["note"],
                source_page=data["page"],
                status="proposed",
            ))

        db.flush()
        seen_keys: dict[tuple, RdSpecItem] = {}
        rank = {"confirmed": 0, "rejected": 1, "proposed": 2}
        for item in sorted(
            db.execute(select(RdSpecItem).where(RdSpecItem.volume_id == vol.id)).scalars().all(),
            key=lambda i: (rank.get(i.status, 3), i.id),
        ):
            k = _key(item.pos, item.name, item.type_mark, item.unit, item.qty, item.source_page)
            if k in seen_keys:
                db.delete(item)  # точная копия — оставляем лучшую по статусу
            else:
                seen_keys[k] = item

        vol.status = "parsed"
        if page_errors:
            vol.error_message = "; ".join(page_errors)[:500]
        db.commit()
        return {
            "items": len(parsed_items),
            "spec_pages": label,
            "sheets": len(register),
            "ocr_pages": ocr_done,
        }
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        vol = db.get(RdVolume, volume_id)
        if vol is not None:
            vol.status = "failed"
            vol.error_message = str(exc)[:500]
            db.commit()
        return {"items": 0, "spec_pages": vol.spec_pages if vol is not None and vol.spec_pages else ""}


_ALLOWED_ITEM_STATUSES = {"proposed", "confirmed", "rejected"}
_ALLOWED_EDIT_FIELDS = {"pos", "name", "type_mark", "unit", "qty", "note", "manufacturer", "code"}


def _get_item(db: Session, project_id: int, item_id: int) -> RdSpecItem | None:
    return db.execute(
        select(RdSpecItem).where(
            RdSpecItem.id == item_id,
            RdSpecItem.project_id == project_id,
        )
    ).scalar_one_or_none()


def set_item_status(db: Session, project_id: int, item_id: int, status: str) -> RdSpecItem:
    from fastapi import HTTPException

    if status not in _ALLOWED_ITEM_STATUSES:
        raise HTTPException(status_code=422, detail="invalid status")
    item = _get_item(db, project_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    item.status = status
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, project_id: int, item_id: int, fields: dict) -> RdSpecItem:
    from fastapi import HTTPException

    item = _get_item(db, project_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    for key, value in (fields or {}).items():
        if key not in _ALLOWED_EDIT_FIELDS:
            continue
        if key == "qty":
            item.qty = _to_decimal(value)
        else:
            setattr(item, key, value)
    item.status = "confirmed"
    db.commit()
    db.refresh(item)
    return item


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def volume_dict(v: RdVolume, counts: dict | None = None) -> dict:
    data = {
        "id": v.id,
        "project_id": v.project_id,
        "object_name": v.object_name,
        "section": v.section,
        "code": v.code,
        "title": v.title,
        "file_name": v.file_name,
        "provider": v.provider,
        "external_id": v.external_id,
        "size_bytes": v.size_bytes,
        "status": v.status,
        "page_count": v.page_count,
        "spec_pages": v.spec_pages,
        "error_message": v.error_message,
        "created_at": _iso(v.created_at),
        "updated_at": _iso(v.updated_at),
    }
    if counts is not None:
        data["counts"] = counts
    return data


def item_dict(i: RdSpecItem) -> dict:
    return {
        "id": i.id,
        "volume_id": i.volume_id,
        "project_id": i.project_id,
        "sort_order": i.sort_order,
        "pos": i.pos,
        "name": i.name,
        "type_mark": i.type_mark,
        "code": i.code,
        "manufacturer": i.manufacturer,
        "unit": i.unit,
        "qty": str(i.qty) if i.qty is not None else None,
        "note": i.note,
        "source_page": i.source_page,
        "status": i.status,
        "created_at": _iso(i.created_at),
    }


def item_counts(db: Session, volume_id: int) -> dict:
    rows = db.execute(
        select(RdSpecItem.status, func.count(RdSpecItem.id))
        .where(RdSpecItem.volume_id == volume_id)
        .group_by(RdSpecItem.status)
    ).all()
    counts = {"proposed": 0, "confirmed": 0, "rejected": 0}
    for status, cnt in rows:
        if status in counts:
            counts[status] = int(cnt)
    return counts


def sheet_dict(s: RdSheet) -> dict:
    return {
        "id": s.id,
        "volume_id": s.volume_id,
        "page_no": s.page_no,
        "sheet_no": s.sheet_no,
        "sheet_title": s.sheet_title,
        "kind": s.kind,
        "text_source": s.text_source,
        "text_len": s.text_len,
    }


def volume_sheet_count(db: Session, volume_id: int) -> int:
    return int(
        db.execute(
            select(func.count(RdSheet.id)).where(RdSheet.volume_id == volume_id)
        ).scalar_one()
    )

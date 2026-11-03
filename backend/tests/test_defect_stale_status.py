"""Synthetic cached-session transitions; not PostgreSQL interleaving or HTTP proof."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app import defects as svc
from app.models.defects import Defect, DefectComment, DefectPhoto
from app.models.project import Project
from test_defects import world


@pytest.mark.parametrize("new_status", ["fixed", "accepted"])
def test_stale_open_cannot_overwrite_newer_fixed_or_accepted(world, new_status):
    db, project = world["db"], world["p"].id
    made = svc.create_defect(db, project, None, {"title": "Synthetic stale transition"})
    db.commit()
    cached = db.get(Defect, made["id"])
    with Session(db.get_bind()) as other:
        svc.change_status(other, project, None, made["id"], "fixed", "Synthetic repair evidence")
        if new_status == "accepted":
            svc.change_status(other, project, None, made["id"], "accepted", "Synthetic acceptance")
        other.commit()
    assert cached.status == "open"
    with pytest.raises(ValueError):
        svc.change_status(db, project, None, made["id"], "in_progress", "Stale attempt must not be recorded")
    db.rollback()
    actual = db.get(Defect, made["id"])
    assert actual.status == new_status
    assert (actual.closed_at is not None) == (new_status == "accepted")
    comments = db.scalars(select(DefectComment).where(DefectComment.defect_id == actual.id)).all()
    assert len(comments) == (2 if new_status == "accepted" else 1)
    assert all("Stale attempt" not in comment.text for comment in comments)


def test_status_success_preserves_other_caller_edits_and_rolls_back(world):
    db, project = world["db"], world["p"].id
    made = svc.create_defect(db, project, None, {"title": "Synthetic original title"})
    db.commit()
    cached = db.get(Defect, made["id"])
    cached.title = "Synthetic caller edit"
    result = svc.change_status(db, project, None, made["id"], "in_progress", "Synthetic start")
    assert result["title"] == "Synthetic caller edit"
    db.rollback()
    assert db.get(Defect, made["id"]).status == "open"
    assert db.get(Defect, made["id"]).title == "Synthetic original title"
    assert db.scalar(select(func.count()).select_from(DefectComment)) == 0



def test_stale_rejection_does_not_flush_caller_changes(world):
    db, project = world["db"], world["p"].id
    made = svc.create_defect(db, project, None, {"title": "Synthetic original title"})
    db.commit()
    cached = db.get(Defect, made["id"])
    with Session(db.get_bind()) as other:
        svc.change_status(other, project, None, made["id"], "fixed", "Synthetic evidence")
        other.commit()
    organization_id = world["p"].organization_id
    cached.title = "Synthetic pending title"
    pending = Project(name="Synthetic pending project", organization_id=organization_id)
    db.add(pending)
    with pytest.raises(ValueError):
        svc.change_status(db, project, None, made["id"], "in_progress", "Stale attempt")
    assert pending.id is None and pending in db.new
    assert cached.title == "Synthetic pending title" and cached in db.dirty
    with db.no_autoflush:
        assert db.scalar(select(Defect.status).where(Defect.id == cached.id)) == "fixed"
    db.rollback()


@pytest.mark.parametrize("comment", [None, " ", "Synthetic repair explanation"])
def test_removed_after_photo_cannot_be_used_as_evidence_at_update(world, monkeypatch, comment):
    from sqlalchemy.sql.dml import Update
    db, project = world["db"], world["p"].id
    made = svc.create_defect(db, project, None, {"title": "Synthetic disappearing evidence"})
    photo = DefectPhoto(project_id=project, defect_id=made["id"], kind="after", filename="synthetic.jpg", mime_type="image/jpeg", size_bytes=3, data=b"abc")
    db.add(photo)
    db.flush()
    photo_id = photo.id
    db.commit()
    original_execute = db.execute
    interrupted = False

    def remove_photo_before_update(statement, *args, **kwargs):
        nonlocal interrupted
        if not interrupted and isinstance(statement, Update) and statement.table.name == "defects":
            interrupted = True
            with Session(db.get_bind()) as other:
                svc.delete_photo(other, project, made["id"], photo_id)
                other.commit()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", remove_photo_before_update)
    try:
        if not (comment or "").strip():
            with pytest.raises(ValueError, match="фото.*комментарий"):
                svc.change_status(db, project, None, made["id"], "fixed", comment)
            db.rollback()
        else:
            assert svc.change_status(db, project, None, made["id"], "fixed", comment)["status"] == "fixed"
            db.commit()
    finally:
        assert interrupted, "Synthetic interleaving did not run"
    actual = db.get(Defect, made["id"])
    assert actual.status == ("fixed" if (comment or "").strip() else "open")
    assert db.scalar(select(func.count()).select_from(DefectPhoto)) == 0
    comments = db.scalars(select(DefectComment).where(DefectComment.defect_id == actual.id)).all()
    assert len(comments) == (1 if (comment or "").strip() else 0)

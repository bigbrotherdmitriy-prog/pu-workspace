"""Weekly PDF consumes current KS state without changing earlier report snapshots."""
from io import BytesIO
from pypdf import PdfReader
from app import estimates as est, customer_portal as cp, work_log_form
from test_customer_portal import world


def report(world):
    return work_log_form.collect_weekly_report_bounded(world['db'],world['p'].id,'2026-10-01','2026-10-28',row_budget=20)


def test_weekly_pdf_uses_committed_signed_decision_and_preserves_prior_snapshot(world):
    db, pid, aid, uid = world['db'], world['p'].id, world['act']['id'], world['customer'].id
    est.set_act_status(db,pid,aid,'sent')
    db.commit()
    before = report(world)
    assert before['ks_acts'][0]['status'] == 'sent'
    assert before['ks_acts'][0]['customer_status'] == 'pending'
    cp.decide(db,pid,aid,uid,'approved',None)
    est.set_act_status(db,pid,aid,'signed')
    db.commit()
    current = report(world)
    assert current['ks_acts'][0]['status'] == 'signed'
    assert current['ks_acts'][0]['customer_status'] == 'approved'
    assert before['ks_acts'][0]['status'] == 'sent'
    assert before['ks_acts'][0]['customer_status'] == 'pending'
    reader = PdfReader(BytesIO(work_log_form.render_weekly_pdf(current)))
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert 'Недельный отчёт' in text and 'signed' in text and 'approved' in text
    assert not reader.trailer['/Root'].get('/OpenAction')
    assert all(not page.get('/AA') and not page.get('/Annots') for page in reader.pages)


def test_weekly_pdf_excludes_deleted_draft_and_includes_it_after_rollback(world):
    db, pid, aid = world['db'], world['p'].id, world['act']['id']
    assert len(report(world)['ks_acts']) == 1
    est.delete_act(db,pid,aid)
    assert report(world)['ks_acts'] == []
    reader = PdfReader(BytesIO(work_log_form.render_weekly_pdf(report(world))))
    assert len(reader.pages) >= 1
    db.rollback()
    assert len(report(world)['ks_acts']) == 1

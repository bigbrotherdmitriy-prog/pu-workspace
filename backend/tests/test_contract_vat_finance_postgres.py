"""Real PostgreSQL VAT refresh CAS/idempotency; isolated synthetic schema only."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api import execution_finance as api
from app.models.audit_log import AuditLog
from app.models.execution_finance import CashFlowEntry
from app.models.organization_contract import Contract
from app.models.user import User
from test_dds_confirmation_postgres import pg_world  # noqa: F401 - shared disposable-schema fixture


def refresh_payload(engine, row_id, key):
    with Session(engine) as db:
        row = db.get(CashFlowEntry, row_id)
        contract = db.get(Contract, row.contract_id)
        return api.VatSnapshotRefresh(expected_state_hash=api._vat_refresh_state_hash(row),
            expected_contract_record_version=contract.record_version, idempotency_key=key)


def test_parallel_identical_refresh_has_one_change_and_one_audit(pg_world):
    engine, _batch, (user_id, row_id) = pg_world
    request = refresh_payload(engine, row_id, "synthetic-pg-vat-same-key")
    barrier = Barrier(2)
    def run():
        with Session(engine) as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=20)
            return api.refresh_vat_snapshot("cash-flow", row_id, request, db, user)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        results = [future.result(timeout=40) for future in futures]
    assert sorted(result["replayed"] for result in results) == [False, True]
    with Session(engine) as db:
        row = db.get(CashFlowEntry, row_id)
        assert row.record_version == 2 and row.vat_snapshot["mode"] == "unspecified"
        assert row.planned_amount == Decimal("1.00") and row.status == "proposed"
        assert db.query(AuditLog).filter_by(action="finance_vat_refreshed").count() == 1


def test_parallel_different_refresh_keys_allow_only_one_state_cas(pg_world):
    engine, _batch, (user_id, row_id) = pg_world
    requests = [refresh_payload(engine, row_id, f"synthetic-pg-vat-key-{i}") for i in range(2)]
    barrier = Barrier(2)
    def run(request):
        with Session(engine) as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=20)
            try:
                api.refresh_vat_snapshot("cash-flow", row_id, request, db, user)
                return 200
            except HTTPException as exc:
                return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, request) for request in requests]
        assert sorted(future.result(timeout=40) for future in futures) == [200, 409]
    with Session(engine) as db:
        assert db.get(CashFlowEntry, row_id).record_version == 2
        assert db.query(AuditLog).filter_by(action="finance_vat_refreshed").count() == 1


def test_another_transaction_contract_edit_rejects_refresh_without_writes(pg_world):
    engine, _batch, (user_id, row_id) = pg_world
    request = refresh_payload(engine, row_id, "synthetic-pg-vat-stale-contract")
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, row_id)
        contract = writer.get(Contract, row.contract_id)
        contract.vat_mode, contract.vat_rate = "rate", Decimal("22.00")
        contract.record_version += 1
        writer.commit()
    with Session(engine) as db:
        with pytest.raises(HTTPException) as exc:
            api.refresh_vat_snapshot("cash-flow", row_id, request, db, db.get(User, user_id))
        assert exc.value.status_code == 409 and "CONTRACT_VERSION_MISMATCH" in str(exc.value.detail)
        row = db.get(CashFlowEntry, row_id)
        assert row.record_version == 1 and row.vat_snapshot is None
        assert db.query(AuditLog).filter_by(action="finance_vat_refreshed").count() == 0

from time import perf_counter

from app.organizer_engine.planner import build_proposal
from app.organizer_engine.types import DriveFile


def test_planner_handles_ten_thousand_metadata_objects():
    files = [
        DriveFile(str(index), f"Документ проекта {index}.pdf", "application/pdf", "root", size=100)
        for index in range(10_000)
    ]
    started = perf_counter()
    proposal = build_proposal(files, project_name="Нагрузочная проверка")
    elapsed = perf_counter() - started
    assert len(proposal) == 10_000
    assert elapsed < 10.0

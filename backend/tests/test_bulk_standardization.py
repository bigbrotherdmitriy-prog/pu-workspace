from types import SimpleNamespace

from app.organizer_engine.config import DEFAULT_FOLDER, FOLDER_STRUCTURE
from app.organizer_engine.executor import OrganizerExecutor
from app.organizer_engine.naming import build_standard_name
from app.organizer_engine.types import DriveFile, FOLDER_MIME


def test_standard_name_keeps_subject_and_marks_gpr_dds():
    assert build_standard_name("ГПР объект.xlsx", "01_УПРАВЛЕНИЕ ПРОЕКТОМ", "Проект А") == (
        "Проект А — ГПР — ГПР объект.xlsx"
    )
    assert build_standard_name("ДДС август.XLSX", "03_ФИНАНСЫ И СМЕТЫ", "Проект А") == (
        "Проект А — ДДС — ДДС август.xlsx"
    )
    assert build_standard_name("неясный файл.PDF", DEFAULT_FOLDER, "Проект А") == (
        "Проект А — Неразобранное — неясный файл.pdf"
    )


def test_standard_name_is_idempotent_and_collapses_duplicate_prefixes():
    once = "Проект А — ДДС — ДДС август.xlsx"
    twice = "Проект А — ДДС — Проект А — ДДС — ДДС август.xlsx"
    assert build_standard_name(once, "03_ФИНАНСЫ И СМЕТЫ", "Проект А") == once
    assert build_standard_name(twice, "03_ФИНАНСЫ И СМЕТЫ", "Проект А") == once


class Repo:
    def __init__(self):
        self.db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
        self.logged = []

    def proposal(self, _proposal_id):
        return {
            "status": "applied",
            "originals_modified": False,
            "copy_folder_id": "copy",
            "session_id": 3,
        }

    def proposal_items(self, _proposal_id):
        return [{"file_id": "f1", "user_decision": "skipped", "target_folder": DEFAULT_FOLDER}]

    def operations(self, _proposal_id):
        return []

    def log_operation(self, *args):
        self.logged.append(args)


class Drive:
    def __init__(self):
        self.files = {
            "f1": DriveFile("f1", "счёт №1.PDF", "application/pdf", "copy"),
        }
        self.children = {"copy": ["f1"]}
        for index, (name, _) in enumerate(FOLDER_STRUCTURE):
            folder_id = f"folder-{index}"
            self.files[folder_id] = DriveFile(folder_id, name, FOLDER_MIME, "copy")
            self.children["copy"].append(folder_id)
            self.children[folder_id] = []

    def list_children(self, parent_id):
        return [self.files[file_id] for file_id in self.children[parent_id]]

    def create_folder(self, *_args):
        raise AssertionError("folders already exist")

    def assert_inside_copy(self, file_id, root_id):
        assert root_id == "copy" and file_id in self.files

    def get_file_meta(self, file_id):
        return self.files[file_id]

    def rename_file(self, file_id, name, _root_id):
        self.files[file_id].name = name

    def move_file(self, file_id, target_parent, source_parent, _root_id):
        self.children[source_parent].remove(file_id)
        self.children[target_parent].append(file_id)
        self.files[file_id].parent_id = target_parent


class PartlyBrokenDrive(Drive):
    def __init__(self):
        super().__init__()
        self.files["broken"] = DriveFile("broken", "битый.pdf", "application/pdf", "copy")
        self.children["copy"].append("broken")

    def get_file_meta(self, file_id):
        if file_id == "broken":
            raise RuntimeError("simulated missing provider object")
        return super().get_file_meta(file_id)


def test_bulk_standardization_changes_safe_copy_and_logs_rollback_data():
    repo = Repo()
    drive = Drive()
    result = OrganizerExecutor(repo, drive).standardize_remaining(8, "Проект А")

    assert result == {"renamed": 1, "moved": 1, "skipped": 0, "errors": 0}
    assert drive.files["f1"].name == "Проект А — Неразобранное — счёт №1.pdf"
    assert drive.files["f1"].parent_id == next(
        file_id for file_id, item in drive.files.items() if item.name == DEFAULT_FOLDER
    )
    assert [entry[3] for entry in repo.logged] == ["standardize_rename", "standardize_move"]
    assert repo.logged[0][4]["name"] == "счёт №1.PDF"


def test_one_broken_file_does_not_rollback_successful_files():
    repo = Repo()
    repo.proposal_items = lambda _proposal_id: [
        {"file_id": "broken", "user_decision": "skipped", "target_folder": DEFAULT_FOLDER},
        {"file_id": "f1", "user_decision": "skipped", "target_folder": DEFAULT_FOLDER},
    ]
    drive = PartlyBrokenDrive()

    result = OrganizerExecutor(repo, drive).standardize_remaining(8, "Проект А")

    assert result == {"renamed": 1, "moved": 1, "skipped": 0, "errors": 1}
    assert drive.files["f1"].name.startswith("Проект А — Неразобранное")
    assert [entry[3] for entry in repo.logged] == ["standardize_rename", "standardize_move"]

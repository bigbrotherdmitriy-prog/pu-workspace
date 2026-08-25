import unittest

from app.organizer_engine.planner import build_proposal
from app.organizer_engine.types import DriveFile


def file(file_id, name, parent="root", md5=None, size=10, modified=None):
    return DriveFile(file_id, name, "application/pdf", parent, md5, size, modified)


class PlannerSafetyTests(unittest.TestCase):
    def test_confident_document_gets_lossless_proposed_name(self):
        item = build_proposal(
            [file("1", "Договор поставки №42 2026-08-20.pdf")],
            project_name="Проект Альфа",
        )[0]

        self.assertEqual(item.proposed_folder, "02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ")
        self.assertIn("Договор поставки №42 2026-08-20", item.proposed_name)
        self.assertTrue(item.proposed_name.endswith(".pdf"))
        self.assertEqual(item.kind, "rename_move")

    def test_exact_duplicates_are_advisory_and_keep_names(self):
        items = build_proposal([
            file("1", "Договор А.pdf", md5="same"),
            file("2", "Договор А копия.pdf", md5="same"),
        ], project_name="Проект")

        self.assertEqual({x.special_case for x in items}, {"duplicate"})
        self.assertEqual([x.proposed_name for x in items], [x.current_name for x in items])
        self.assertNotIn("delete", {x.kind for x in items})

    def test_probable_versions_require_review_and_keep_names(self):
        items = build_proposal([
            file("1", "Смета объекта v1.pdf"),
            file("2", "Смета объекта v2.pdf"),
        ], project_name="Проект")

        self.assertEqual({x.special_case for x in items}, {"version"})
        self.assertTrue(all(x.proposed_name == x.current_name for x in items))

    def test_ambiguous_file_goes_to_inbox_without_rename(self):
        item = build_proposal([file("1", "scan0001.pdf")], project_name="Проект")[0]
        self.assertEqual(item.proposed_folder, "00_НЕРАЗОБРАННОЕ")
        self.assertEqual(item.proposed_name, item.current_name)
        self.assertEqual(item.special_case, "ambiguous")

    def test_document_content_classifies_generic_filename(self):
        generic = file("1", "scan0001.pdf")
        generic.content_text = "ДОГОВОР ПОСТАВКИ. Стороны заключили настоящий договор."
        item = build_proposal([generic], project_name="Проект")[0]
        self.assertEqual(item.proposed_folder, "02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ")
        self.assertGreaterEqual(item.confidence, 0.9)
        self.assertIn("тексту документа", item.reasoning)


if __name__ == "__main__":
    unittest.main()

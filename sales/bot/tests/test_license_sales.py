import unittest

from app.knowledge import ApprovedKnowledgeProvider
from app.service import main_menu


class LicenseSalesTest(unittest.TestCase):
    def test_menu_offers_standalone_license(self) -> None:
        labels = [button["text"] for row in main_menu()["inline_keyboard"] for button in row]
        self.assertIn("📦 Запросить лицензию", labels)

    def test_price_answer_does_not_promise_support_or_unapproved_fixed_price(self) -> None:
        answer = ApprovedKnowledgeProvider().answer("Сколько стоит лицензия?")
        self.assertIn("без обязательного сопровождения", answer)
        self.assertIn("состав поставки", answer)


if __name__ == "__main__":
    unittest.main()

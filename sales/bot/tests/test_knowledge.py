import unittest

from app.knowledge import ApprovedKnowledgeProvider


class ApprovedKnowledgeProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = ApprovedKnowledgeProvider()

    def test_does_not_claim_all_integrations_are_ready(self) -> None:
        answer = self.provider.answer("Есть интеграции с Яндекс и Microsoft?")
        self.assertIn("roadmap", answer)
        self.assertIn("Google Workspace", answer)

    def test_ready_license_status_is_explicit(self) -> None:
        answer = self.provider.answer("Продукт уже готов?")
        self.assertIn("готовую самостоятельную лицензионную версию", answer)
        self.assertIn("конкретный проверенный релиз", answer)

    def test_external_ai_can_be_forbidden(self) -> None:
        answer = self.provider.answer("Как защищены данные?")
        self.assertIn("запрещать передачу документов внешней модели", answer)


if __name__ == "__main__":
    unittest.main()

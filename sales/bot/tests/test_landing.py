import unittest
from pathlib import Path


LANDING = Path(__file__).parents[2] / "landing"


class SalesLandingTest(unittest.TestCase):
    def test_home_promotes_standalone_license(self) -> None:
        source = (LANDING / "index.html").read_text(encoding="utf-8")
        self.assertIn("ГОТОВАЯ САМОСТОЯТЕЛЬНАЯ ВЕРСИЯ", source)
        self.assertIn("без обязательного обслуживания", source)
        self.assertNotIn("LIMITED EARLY ACCESS", source)

    def test_license_page_has_clear_scope_and_lead_source(self) -> None:
        source = (LANDING / "license.html").read_text(encoding="utf-8")
        self.assertIn("Без обязательного обслуживания", source)
        self.assertIn("исходным кодом", source)
        self.assertIn("/go.html?source=license_page", source)

    def test_home_has_conversion_sections_and_distinct_lead_sources(self) -> None:
        source = (LANDING / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="compare"', source)
        self.assertIn('id="purchase"', source)
        self.assertIn('id="faq"', source)
        self.assertIn("ГПР, бюджет и ДДС", source)
        self.assertIn("/go.html?source=package_license", source)
        self.assertIn("/go.html?source=diagnostic_bar", source)
        self.assertIn("Значимые действия подтверждает человек", source)

    def test_tracking_redirect_is_first_party_and_validates_source(self) -> None:
        source = (LANDING / "go.html").read_text(encoding="utf-8")
        self.assertIn("URLSearchParams", source)
        self.assertIn("puworkspace_bot?start=", source)
        self.assertIn("^[A-Za-z0-9_-]{1,64}$", source)


if __name__ == "__main__":
    unittest.main()

import json
import re
import unittest
from pathlib import Path


LANDING = Path(__file__).parents[2] / "landing"


class SalesLandingTest(unittest.TestCase):
    def test_home_promotes_standalone_license(self) -> None:
        source = (LANDING / "index.html").read_text(encoding="utf-8")
        self.assertIn("КОНТРОЛИРУЕМЫЙ ПИЛОТ V5.4", source)
        self.assertIn("САМОСТОЯТЕЛЬНАЯ ЛИЦЕНЗИЯ", source)
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
        self.assertIn('type="application/ld+json"', source)
        self.assertIn('"@type": "FAQPage"', source)
        self.assertIn('rel="canonical" href="https://puworkspace.ru/"', source)
        structured = re.search(
            r'<script type="application/ld\+json">(.*?)</script>', source, re.DOTALL
        )
        self.assertIsNotNone(structured)
        self.assertEqual(json.loads(structured.group(1))["@context"], "https://schema.org")

    def test_home_immersive_layer_is_local_accessible_and_value_led(self) -> None:
        source = (LANDING / "index.html").read_text(encoding="utf-8")
        styles = (LANDING / "home.css").read_text(encoding="utf-8")
        experience = (LANDING / "experience.js").read_text(encoding="utf-8")

        self.assertIn('src="experience.js?', source)
        self.assertIn('class="immersive-field"', source)
        self.assertIn('class="values"', source)
        self.assertIn("Ответственность остаётся у человека", source)
        self.assertIn("prefers-reduced-motion", styles)
        self.assertIn("IntersectionObserver", experience)
        self.assertIn("requestAnimationFrame", experience)
        self.assertNotIn("fetch(", experience)
        self.assertNotRegex(experience, r"https?://")

    def test_tracking_redirect_is_first_party_and_validates_source(self) -> None:
        source = (LANDING / "go.html").read_text(encoding="utf-8")
        self.assertIn("URLSearchParams", source)
        self.assertIn("puworkspace_bot?start=", source)
        self.assertIn("^[A-Za-z0-9_-]{1,64}$", source)


if __name__ == "__main__":
    unittest.main()

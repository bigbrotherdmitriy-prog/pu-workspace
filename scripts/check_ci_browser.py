"""Exercise the compiled UI against a disposable loopback-only stack."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from check_ci_smoke import environment


SYNTHETIC_DOCUMENT = "browser-e2e-upload.txt"
SYNTHETIC_CONTENT = (
    "Просим подготовить тестовый акт выполненных работ до 30.12.2026. "
    "Ответственный: Тестовый сотрудник. Все сведения синтетические."
)
FORBIDDEN_EXTERNAL_PATHS = (
    "/google/auth",
    "/google/files",
    "/gmail/sync",
    "/send-gmail",
)


def browser_base(env: dict[str, str]) -> str:
    """Fail closed before the browser can mutate anything outside isolated CI."""
    port = int(env["PU_TEST_PORT"])
    base = f"http://127.0.0.1:{port}"
    parsed = urlparse(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.port != port
        or not 1024 <= port <= 65535
        or port == 3000
    ):
        raise ValueError("Browser smoke writes require an isolated loopback port")
    return base


def run(env_file: str, report_dir: Path) -> dict[str, object]:
    # Import lazily so safety tests do not need a browser runtime installed.
    from playwright.sync_api import expect, sync_playwright

    env = environment(env_file)
    base = browser_base(env)
    failures: list[str] = []
    forbidden_requests: list[str] = []
    external_requests: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.on("pageerror", lambda error: failures.append(type(error).__name__))

        def guard_request(route, request) -> None:
            parsed = urlparse(request.url)
            is_external = parsed.hostname not in {"localhost", "127.0.0.1"}
            is_forbidden = any(marker in parsed.path for marker in FORBIDDEN_EXTERNAL_PATHS)
            if is_external:
                external_requests.append(f"{request.method} {parsed.hostname}{parsed.path}")
            if is_forbidden:
                forbidden_requests.append(f"{request.method} {parsed.path}")
            if is_external or is_forbidden:
                route.abort("blockedbyclient")
            else:
                route.continue_()

        page.route("**/*", guard_request)
        page.goto(f"{base}/new/", wait_until="networkidle")
        expect(page.get_by_role("heading", name="Вход в PU Workspace")).to_be_visible()
        page.get_by_label("Email", exact=True).fill("ci-admin@example.test")
        page.get_by_label("Пароль", exact=True).fill(env["PU_SMOKE_PASSWORD"])
        page.get_by_role("button", name="Войти", exact=True).click()
        expect(page.get_by_role("heading", name="Вход в PU Workspace")).not_to_be_visible(timeout=20_000)
        expect(page.get_by_text("Рабочий центр", exact=True).first).to_be_visible()

        selector = page.locator("header select")
        selector.select_option(label="CI project A")
        selected_id = selector.input_value()

        # Integrations: inspect status but deliberately never invoke Google/Gmail.
        page.locator("aside nav").get_by_role(
            "button", name="Интеграции", exact=True,
        ).click()
        expect(page.get_by_role("heading", name="Интеграции", level=1)).to_be_visible()
        expect(page.get_by_role("heading", name="Google Drive", level=2)).to_be_visible()
        expect(page.get_by_role("heading", name="Gmail", level=2)).to_be_visible()
        expect(page.get_by_role("heading", name="Локальная рабочая папка", level=2)).to_be_visible()

        # Upload a deterministic local text file through the real UI.
        page.get_by_role("button", name="Загрузить папку", exact=True).click()
        dialog = page.get_by_role("dialog", name="Загрузка документов")
        expect(dialog).to_be_visible()
        dialog.locator('input[type="file"]').first.set_input_files({
            "name": SYNTHETIC_DOCUMENT,
            "mimeType": "text/plain",
            "buffer": SYNTHETIC_CONTENT.encode("utf-8"),
        })
        submit = dialog.get_by_role(
            "button", name="Загрузить и проанализировать (1)", exact=True,
        )
        expect(submit).to_be_enabled()
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.endswith("/local-upload/analyze")
        ) as upload_info:
            submit.click()
        assert upload_info.value.ok, "Synthetic local upload failed"
        expect(dialog).not_to_be_visible(timeout=20_000)
        expect(page.get_by_text(re.compile(r"Обработано:\s*1\."))).to_be_visible()

        # Documents: verify the uploaded file appears and its detail can open.
        page.locator("aside nav").get_by_role(
            "button", name="Документы", exact=True,
        ).click()
        expect(page.get_by_role("heading", name="Реестр документов")).to_be_visible()
        uploaded_document = page.get_by_text(SYNTHETIC_DOCUMENT, exact=True).first
        expect(uploaded_document).to_be_visible(timeout=20_000)
        uploaded_document.click()
        expect(page.get_by_role("heading", name=SYNTHETIC_DOCUMENT, level=2)).to_be_visible()

        # Tasks: the same synthetic upload must create a source-linked task.
        page.locator("aside nav").get_by_role(
            "button", name="Задачи", exact=True,
        ).click()
        expect(page.get_by_role("heading", name="Реестр задач")).to_be_visible()
        page.get_by_role("button", name="Все", exact=True).click()
        expect(page.get_by_text(SYNTHETIC_DOCUMENT, exact=False).first).to_be_visible()
        expect(page.get_by_text(re.compile(r"подготовить тестовый акт", re.I)).first).to_be_visible()

        # Project isolation and persisted project choice remain covered.
        selector.select_option(label="CI project B")
        page.locator("aside nav").get_by_role(
            "button", name="Документы", exact=True,
        ).click()
        expect(page.get_by_text("Документы не найдены", exact=True)).to_be_visible()
        expect(page.get_by_text(SYNTHETIC_DOCUMENT, exact=True)).not_to_be_visible()
        selector.select_option(label="CI project A")
        expect(page.get_by_text(SYNTHETIC_DOCUMENT, exact=True).first).to_be_visible()
        page.reload(wait_until="networkidle")
        expect(page.locator("header select")).to_have_value(selected_id)

        # Re-open Integrations after the mutation to catch navigation/state regressions.
        page.locator("aside nav").get_by_role(
            "button", name="Интеграции", exact=True,
        ).click()
        expect(page.get_by_role("heading", name="Локальная рабочая папка", level=2)).to_be_visible()

        assert not failures, f"Browser JavaScript exceptions occurred: {failures}"
        assert not forbidden_requests, f"Forbidden integration requests occurred: {forbidden_requests}"
        assert not external_requests, f"Browser contacted non-loopback hosts: {external_requests}"

        report_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=report_dir / "browser.png", full_page=True)
        result = {
            "ready": True,
            "login": True,
            "project_selection": True,
            "local_upload": True,
            "documents": True,
            "tasks": True,
            "integrations_read_only": True,
            "project_isolation": True,
            "external_requests": 0,
        }
        (report_dir / "browser.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        context.close()
        browser.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env.ci")
    parser.add_argument("--report-dir", default="ci-reports")
    args = parser.parse_args()
    result = run(args.env_file, Path(args.report_dir))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

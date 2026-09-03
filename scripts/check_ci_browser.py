"""Exercise the actual compiled UI against the disposable stack."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, expect
from check_ci_smoke import environment


def main():
    env = environment('.env.ci')
    port = int(env['PU_TEST_PORT'])
    if port == 3000:
        raise ValueError('Production port is forbidden')
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.on('pageerror', lambda error: failures.append(type(error).__name__))
        page.goto(f'http://127.0.0.1:{port}/new/')
        expect(page.get_by_role('heading', name='Вход в PU Workspace')).to_be_visible()
        page.get_by_label('Email', exact=True).fill('ci-admin@example.test')
        page.get_by_label('Пароль', exact=True).fill(env['PU_SMOKE_PASSWORD'])
        page.get_by_role('button', name='Войти', exact=True).click()
        expect(page.get_by_role('heading', name='Вход в PU Workspace')).not_to_be_visible(timeout=20000)
        # The navigation must come from the real application, not the legacy UI.
        expect(page.get_by_text('Рабочий центр', exact=True).first).to_be_visible()
        selector = page.locator('header select')
        selector.select_option(label='CI project A')
        selected_id = selector.input_value()
        page.get_by_text('Документы', exact=True).first.click()
        expect(page.get_by_role('heading', name='Реестр документов')).to_be_visible()
        document = page.get_by_text(re.compile(r'acceptance\.txt')).first
        expect(document).to_be_visible()
        selector.select_option(label='CI project B')
        expect(page.get_by_text('Документы не найдены', exact=True)).to_be_visible()
        expect(document).not_to_be_visible()
        selector.select_option(label='CI project A')
        expect(document).to_be_visible()
        page.reload()
        expect(page.locator('header select')).to_have_value(selected_id)
        assert not failures, 'Browser JavaScript exceptions occurred'
        Path('ci-reports').mkdir(exist_ok=True)
        page.screenshot(path='ci-reports/browser.png', full_page=True)
        browser.close()
    print('Browser login and document navigation passed')


if __name__ == '__main__':
    main()

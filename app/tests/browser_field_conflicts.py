"""Optional real-browser check: conflicts only when the same typed field changed elsewhere.

Requires external Playwright; never a runtime dependency. Runs in a temporary project.

    python app/tests/browser_field_conflicts.py --browser webkit
    python app/tests/browser_field_conflicts.py --browser "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
"""
import argparse
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.tests.helpers import temporary_project
from app.server.main import LearningHub, HubHTTPServer
from playwright.sync_api import sync_playwright


def run(browser_path):
    failed, notes, rejected = [], [], []
    expect = lambda label, ok: (notes if ok else failed).append(label)
    with temporary_project(static=True) as root, sync_playwright() as playwright:
        (root / 'downloads').mkdir()
        hub = LearningHub(root, downloads=root / 'downloads')
        server = HubHTTPServer(('127.0.0.1', 0), hub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = 'http://127.0.0.1:%d' % server.server_address[1]
        browser = (playwright.webkit.launch(headless=True) if browser_path == 'webkit'
                   else playwright.chromium.launch(executable_path=browser_path, headless=True))
        context = browser.new_context(viewport={'width': 1280, 'height': 800})

        def window():
            page = context.new_page()
            page.on('response', lambda r: r.status >= 400 and rejected.append('%s %s' % (r.status, (r.request.post_data or r.url)[:120])))
            return page

        a, b = window(), window()
        a.goto(origin); a.click('.welcome .primary'); a.wait_for_selector('.article h1')
        card = a.evaluate("fetch('/api/bootstrap').then(r=>r.json()).then(d=>d.cards[0].id)")
        summary = lambda: hub.store.read()['progress']['cards'][card].get('summary')
        for page in (a, b):
            page.goto(origin + '/#card/%s/wrap' % card); page.reload(); page.wait_for_selector('#summary-input')

        # 1. One window keeps reading (positions, phase) while the other writes: never a conflict.
        b.fill('#summary-input', '第一版总结'); b.wait_for_timeout(300)
        for i in range(4):
            a.goto(origin + '/#card/%s/learn' % card); a.wait_for_selector('.article h1')
            a.evaluate('y => window.scrollTo(0, y)', 300 + 200 * i); a.wait_for_timeout(700)
            b.fill('#summary-input', '第一版总结' + '。' * (i + 1)); b.wait_for_timeout(700)
        b.wait_for_timeout(2500)
        expect('typing in one window while the other reads saves every version', summary() == '第一版总结。。。。')
        expect('no conflict notice while only positions changed', b.locator('.service-notice').filter(has_text='别处').count() == 0)
        expect('no 409 so far', not [r for r in rejected if r.startswith('409')])

        # 2. Rapid successive saves of one field from one window: no false conflict.
        for i in range(6):
            b.fill('#summary-input', '快速修改第 %d 次' % i); b.wait_for_timeout(120)
        b.wait_for_timeout(2500)
        expect('rapid edits end with the last text saved (got %r)' % summary(), summary() == '快速修改第 5 次')
        expect('rapid edits produce no 409', not [r for r in rejected if r.startswith('409')])

        # 3. The other window still shows an old summary and starts typing: a real conflict.
        a.goto(origin + '/#card/%s/wrap' % card); a.reload(); a.wait_for_selector('#summary-input')
        b.fill('#summary-input', '另一个窗口后来写的'); b.wait_for_timeout(2500)
        a.fill('#summary-input', a.input_value('#summary-input') + '——在旧内容上接着写'); a.wait_for_timeout(3000)
        a_text = a.input_value('#summary-input')
        expect('the newer text from the other window is not overwritten (got %r)' % summary(), summary() == '另一个窗口后来写的')
        expect('the stale window is told about the conflict', a.locator('.service-notice').filter(has_text='别处').count() >= 1)
        drafts = [d for d in hub.drafts.list()['drafts'] if d['card'] == card and d['field'] == 'summary']
        expect('the stale window keeps its text as a draft', any(d['text'] == a_text for d in drafts))
        browser.close(); server.shutdown(); server.server_close()
    return {'passed': notes, 'failed': failed, 'rejected': rejected[:6]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', required=True, help='Chromium-family executable path, or "webkit"')
    result = run(parser.parse_args().browser)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    sys.exit(1 if result['failed'] else 0)

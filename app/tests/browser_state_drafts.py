"""Optional real-browser check for CODEX_BRIEF 18.8 (reading position is not a draft).

Requires external Playwright; never a runtime dependency. Runs in a temporary project.

    python app/tests/browser_state_drafts.py --browser webkit
    python app/tests/browser_state_drafts.py --browser "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
"""
import argparse
import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.tests.helpers import temporary_project
from app.server.main import LearningHub, HubHTTPServer
from playwright.sync_api import sync_playwright

FINISH = '''async ids => {
  let rev = (await (await fetch('/api/bootstrap')).json()).revision;
  const op = async body => { const r = await fetch('/api/op', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({...body, revision: rev})}); const d = await r.json(); if (!r.ok) throw new Error(d.message); rev = d.revision; };
  for (const id of ids) {
    const card = (await (await fetch('/api/card/' + id)).json()).card;
    await op({op:'start_card', card:id});
    for (const q of card.questions || []) await op({op:'mark_question', card:id, q:q.id, mark:'correct'});
    await op({op:'complete', card:id});
  }
}'''


def run(browser_path):
    failed, notes, rejected = [], [], []
    expect = lambda label, ok: (notes if ok else failed).append(label)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    with temporary_project(static=True) as root, sync_playwright() as playwright:
        (root / 'downloads').mkdir()
        def serve():
            hub = LearningHub(root, downloads=root / 'downloads')
            server = HubHTTPServer(('127.0.0.1', port), hub)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            return hub, server
        hub, server = serve()
        origin = 'http://127.0.0.1:%d' % port
        browser = playwright.webkit.launch(headless=True) if browser_path == 'webkit' else playwright.chromium.launch(executable_path=browser_path, headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 800})
        def page_for():
            page = context.new_page()
            page.on('response', lambda r: r.status >= 400 and rejected.append('%s %s' % (r.status, (r.request.post_data or r.url)[:100])))
            return page
        page = page_for()
        page.goto(origin); page.click('.welcome .primary'); page.wait_for_selector('.article h1')
        ids = page.evaluate("fetch('/api/bootstrap').then(r=>r.json()).then(d=>d.cards.map(c=>c.id))")
        page.evaluate(FINISH, ids[:2]); card = ids[2]
        page.goto(origin + '/#today'); page.reload(); page.wait_for_selector('.hero-card')
        live = lambda: [d for d in hub.drafts.list()['drafts']]
        def open_card():
            page.goto(origin + '/#route'); page.wait_for_selector('#node-' + card)
            page.click('#node-%s .node-content' % card); page.wait_for_selector('.article h1')
        def leave():
            page.click('.card-topbar [data-action="nav"]'); page.wait_for_selector('.route')

        for i in range(20):
            open_card(); page.wait_for_timeout(800); leave(); page.wait_for_timeout(200)
        for i in range(20):
            open_card(); page.wait_for_timeout(200); page.mouse.wheel(0, 200 + 30 * i); page.wait_for_timeout(600); leave()
        page.wait_for_timeout(3000)
        expect('open/leave within 2s x40: no drafts', not live())
        expect('open/leave: no draft notice', page.locator('.draft-global, .draft-notice').count() == 0)

        # Return to the place: read to a middle section, leave quickly, reopen.
        open_card(); page.wait_for_timeout(400)
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight * 0.45)"); page.wait_for_timeout(500)
        before = page.evaluate("document.querySelector('.toc-link.current')?.dataset.anchor")
        leave(); page.wait_for_timeout(300); open_card(); page.wait_for_timeout(1200)
        after = page.evaluate("document.querySelector('.toc-link.current')?.dataset.anchor")
        expect('reopen after leaving returns to %s (got %s)' % (before, after), bool(before) and before == after)
        # Quit right after reading further (closing the window is a new window next time).
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight * 0.7)"); page.wait_for_timeout(500)
        before = page.evaluate("document.querySelector('.toc-link.current')?.dataset.anchor")
        page.close(); page = page_for(); page.wait_for_timeout(1500)
        page.goto(origin + '/#today'); page.wait_for_selector('.hero-card'); open_card(); page.wait_for_timeout(1200)
        after = page.evaluate("document.querySelector('.toc-link.current')?.dataset.anchor")
        expect('reopen after quitting returns to %s (got %s)' % (before, after), bool(before) and before == after)
        expect('no drafts after quitting', not live())

        # Typed text is still protected when the service is down.
        page.goto(origin + '/#card/%s/wrap' % card); page.wait_for_selector('#summary-input')
        server.shutdown(); server.server_close()
        page.fill('#summary-input', '后台断开时写的一句话'); page.wait_for_timeout(1500)
        stored = page.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('learning-hub-pending-v2:')&&k.endsWith(':summary')).length")
        expect('typed text kept as a draft while the service is down', stored >= 1)
        hub, server = serve(); page.wait_for_timeout(9000)
        expect('typed text saved after reconnect', hub.store.read()['progress']['cards'][card].get('summary') == '后台断开时写的一句话')
        expect('no 409 or other rejected requests', not [r for r in rejected if r.startswith('409')])
        browser.close(); server.shutdown()
    return {'passed': notes, 'failed': failed, 'rejected': rejected[:5]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', required=True, help='Chromium-family executable path, or "webkit"')
    result = run(parser.parse_args().browser)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    sys.exit(1 if result['failed'] else 0)

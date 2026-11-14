import json
import os
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.service import create_server


class BrowserContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server = create_server(state_dir=self.temporary.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def test_current_workflow_in_real_browser(self):
        root = Path(__file__).resolve().parents[1]
        action = json.loads((root / 'data/default-action.json').read_text())
        request = Request(self.base + '/api/action', json.dumps(action).encode(),
                          {'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            expected = json.load(response)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=['--no-sandbox'] if os.geteuid() == 0 else [])
            try:
                page = browser.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(self.base, wait_until='networkidle')
                page.wait_for_function("document.querySelector('#summary').textContent !== '载入中'")
                self.assertNotIn('error', json.loads(page.locator('#summary').inner_text()))
                sources = page.locator('#source option').all_text_contents()
                self.assertTrue(sources)
                for source in sources:
                    page.select_option('#source', source)
                    self.assertGreater(page.locator('#records tbody tr').count(), 0)
                page.locator('#action-form button').click()
                page.wait_for_function("document.querySelector('#result').textContent !== '选择记录后操作'")
                self.assertEqual(json.loads(page.locator('#result').inner_text()), expected)
                page.locator('#note').fill('复查当前业务资料与结果')
                page.locator('#note-form button').click()
                page.wait_for_selector('#notes li')
                page.reload(wait_until='networkidle')
                self.assertEqual(page.locator('#notes li').count(), 1)
                self.assertIn('复查当前业务资料与结果', page.locator('#notes li').inner_text())
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()

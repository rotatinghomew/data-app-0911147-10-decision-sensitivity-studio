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

    def test_global_sensitivity_workflow_in_real_browser(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=['--no-sandbox'] if os.geteuid() == 0 else [])
            try:
                page = browser.new_page()
                page.set_default_timeout(60000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(self.base, wait_until='networkidle')
                page.wait_for_function(
                    "document.querySelector('#sensitivity-status').textContent.startsWith('完成')")

                # 贡献表可回查：每个目标都有三行输入与区间
                self.assertGreater(page.locator('.tabs button:not([disabled])').count(), 0)
                rows = page.locator('.contribution-block table').first.locator('tbody tr')
                self.assertEqual(rows.count(), 3)
                self.assertIn('participants', rows.nth(0).inner_text())
                self.assertRegex(rows.nth(0).inner_text(), r'\d')

                # 切换到超支概率与方案总成本差，内容确实变化
                tabs = page.locator('.tabs button')
                kinds = [button.inner_text() for button in tabs.all()]
                self.assertIn('超支概率·shared', kinds)
                page.locator('.tabs button', has_text='超支概率·shared').click()
                self.assertEqual(page.locator('.tabs button.active').inner_text(), '超支概率·shared')

                # 交互与适用范围都在页面上
                page.locator('.target-body details summary', has_text='主要交互').click()
                self.assertGreater(page.locator('.target-body details').nth(1).locator('table').count(), 0)
                self.assertIn('范围', page.locator('.card').first.inner_text())
                self.assertIn('内部', page.locator('.card').nth(1).inner_text())
                self.assertIn('补测', page.locator('.card').nth(2).inner_text())

                # 给出测量成本后，建议依据应变为净价值
                for name in ('participants', 'hours', 'hourly_cost'):
                    page.locator('.inline-form input[name="' + name + '"]').fill(
                        {'participants': '900', 'hours': '100', 'hourly_cost': '50'}[name])
                page.locator('.inline-form button').click()
                page.wait_for_function(
                    "document.querySelector('#sensitivity-status').textContent.includes('用时')")
                self.assertIn('净价值', page.locator('.card').nth(2).inner_text())

                # 非法参数给出中文错误，且不破坏已有结果
                page.fill('#sensitivity-form input[name="bootstrap"]', '5')
                page.locator('#sensitivity-form button').click()
                page.wait_for_function(
                    "document.querySelector('#sensitivity-status').textContent.startsWith('分析失败')")
                self.assertIn('自助重复次数', page.locator('#sensitivity-status').inner_text())
                self.assertGreater(page.locator('.tabs button').count(), 0)

                # 批注可以记录本次分析配置
                page.locator('#note').fill('按净价值比较补测')
                page.locator('#note-form button').click()
                page.wait_for_selector('#notes li')
                self.assertIn('按净价值比较补测', page.locator('#notes li').inner_text())
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()

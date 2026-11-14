from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import tempfile
import threading
import unittest

from app.service import create_server
from app.storage import NoteStore


class HttpContractTests(unittest.TestCase):
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

    def request(self, path, payload=None):
        request = Request(self.base + path, None if payload is None else json.dumps(payload).encode(),
                          {'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    def test_live_baseline_action(self):
        status, health = self.request('/health')
        self.assertEqual(status, 200)
        self.assertEqual(health['status'], 'ok')
        status, summary = self.request('/api/summary')
        self.assertEqual(status, 200)
        self.assertTrue(summary)
        action = json.loads((Path(__file__).resolve().parents[1] / 'data/default-action.json').read_text())
        status, result = self.request('/api/action', action)
        self.assertEqual(status, 200)
        self.assertTrue(result)

    def test_note_survives_store_reopen(self):
        status, entry = self.request('/api/notes', {'text': '保留当前记录选择', 'selection': {'table': 'records'}})
        self.assertEqual(status, 201)
        self.assertEqual(NoteStore(Path(self.temporary.name)).list(), [entry])
        self.assertEqual(self.request('/api/notes')[1]['notes'], [entry])

    def test_invalid_action_is_client_error(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/action', {'operation': 'unknown'})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_unknown_path_not_treated_as_index(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/../../etc/passwd')
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_page_assets_available(self):
        for path in ('/', '/app.js', '/style.css'):
            with urlopen(self.base + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())


if __name__ == '__main__':
    unittest.main()

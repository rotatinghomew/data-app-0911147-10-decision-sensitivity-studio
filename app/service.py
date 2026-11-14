from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import argparse
import copy
import json
import os
import threading

from . import domain
from .storage import NoteStore


ROOT = Path(__file__).resolve().parents[1]


def load_study(path=None):
    source = Path(path or os.environ.get('APP_STUDY', ROOT / 'data/study.json'))
    return json.loads(source.read_text(encoding='utf-8'))


def create_server(host='127.0.0.1', port=0, data=None, state_dir=None):
    dataset = copy.deepcopy(load_study() if data is None else data)
    notes = NoteStore(Path(state_dir or os.environ.get('APP_STATE_DIR', ROOT / '.local')))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string, *arguments):
            if os.environ.get('APP_HTTP_LOG') == '1':
                super().log_message(format_string, *arguments)

        def reply(self, status, payload, content_type='application/json; charset=utf-8'):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path).path
            try:
                if route == '/health':
                    self.reply(200, {'status': 'ok'})
                elif route == '/api/study':
                    self.reply(200, dataset)
                elif route == '/api/summary':
                    self.reply(200, domain.baseline(dataset))
                elif route == '/api/notes':
                    self.reply(200, {'notes': notes.list()})
                elif route in ('/', '/app.js', '/style.css'):
                    name = 'index.html' if route == '/' else route[1:]
                    content_type = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name]
                    self.reply(200, (ROOT / 'web' / name).read_bytes(), content_type + '; charset=utf-8')
                else:
                    self.reply(404, {'error': '路径不存在'})
            except (ValueError, KeyError, TypeError) as error:
                self.reply(400, {'error': str(error)})

        def do_POST(self):
            route = urlsplit(self.path).path
            if route not in ('/api/action', '/api/notes'):
                self.reply(404, {'error': '路径不存在'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('请求体长度不合法')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('请求应为对象')
                if route == '/api/action':
                    result = domain.act(dataset, payload)
                    self.reply(200, result)
                else:
                    self.reply(201, notes.append(payload.get('text'), payload.get('selection', {})))
            except (ValueError, KeyError, TypeError) as error:
                self.reply(400, {'error': str(error)})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description='本地分析工作台')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print('工作台地址：http://' + args.host + ':' + str(server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

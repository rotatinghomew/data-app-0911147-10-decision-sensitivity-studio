from pathlib import Path
from urllib.request import Request, urlopen
import json
import tempfile
import threading

from .service import create_server


def main():
    root = Path(__file__).resolve().parents[1]
    request = json.loads((root / 'data/default-action.json').read_text())
    with tempfile.TemporaryDirectory() as directory:
        server = create_server(state_dir=directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            print('本地产品实时运行：' + base)
            for route in ('/health', '/api/summary', '/api/sensitivity/meta'):
                with urlopen(base + route, timeout=5) as response:
                    print(route, json.dumps(json.load(response), ensure_ascii=False, indent=2))
            operation = Request(base + '/api/action', json.dumps(request).encode(), {'Content-Type': 'application/json'})
            with urlopen(operation, timeout=5) as response:
                print('当前操作', json.dumps(json.load(response), ensure_ascii=False, indent=2))
            analysis = Request(base + '/api/sensitivity',
                               json.dumps({'scenario': 'reference', 'bootstrap': 200}).encode(),
                               {'Content-Type': 'application/json'})
            with urlopen(analysis, timeout=60) as response:
                result = json.load(response)
            print('全局敏感性分析', json.dumps({
                'scenario': result['scenario'],
                'decision': result['decision'],
                'targets': [{'id': target['id'], 'contributions': [
                    {'input': item['input'], 'share': item['share'],
                     'share_interval': item['share_interval'], 'rank_interval': item['rank_interval']}
                    for item in target['contributions']],
                    'interactions': target['interactions'],
                    'minimum_r2': target['fit']['minimum_r2']} for target in result['targets']],
                'bounds_only': result['bounds_only'],
                'internal_noise': result['internal_noise'],
                'information': result['information'],
                'diagnostics': result['diagnostics'],
            }, ensure_ascii=False, indent=2))
            print('运行结束；上述为当前版本的实际响应。')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()

from datetime import datetime, timezone
from pathlib import Path
import copy
import json
import os
import threading
import uuid


class NoteStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'notes.json'
        self.lock = threading.Lock()

    def _read(self):
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text(encoding='utf-8'))

    def list(self):
        with self.lock:
            return copy.deepcopy(self._read())

    def append(self, text, selection):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError('批注应为一至两千字')
        if not isinstance(selection, dict):
            raise ValueError('批注选择必须为对象')
        entry = {'id': uuid.uuid4().hex, 'text': text.strip(), 'selection': copy.deepcopy(selection),
                 'created_at': datetime.now(timezone.utc).isoformat()}
        with self.lock:
            entries = self._read()
            entries.append(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('w', encoding='utf-8') as handle:
                    json.dump(entries, handle, ensure_ascii=False, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return entry

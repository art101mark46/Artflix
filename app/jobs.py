"""A single cancellable background library scan; the index itself lives in SQLite."""
import threading
import time
import uuid
import json
import os
import tempfile
from pathlib import Path


class ScanJobs:
    def __init__(self, state_path=None):
        self._state_path=state_path
        self._loaded_path=None
        self._last_saved=0
        self._mutex = threading.RLock()
        self._cancel = threading.Event()
        self._state = {'state': 'idle', 'checked': 0, 'total': 0, 'found': 0, 'new': 0, 'errors': 0, 'current': '', 'elapsed': 0}

    def _load(self):
        if not self._state_path:return
        path=Path(self._state_path() if callable(self._state_path) else self._state_path)
        if self._loaded_path==path:return
        self._loaded_path=path
        try:
            saved=json.loads(path.read_text())
            if isinstance(saved,dict) and saved.get('state') in {'idle','running','cancelling','completed','cancelled','failed','interrupted'}:
                self._state=saved
                if saved['state'] in {'running','cancelling'}:
                    self._state.update(state='interrupted',finished=path.stat().st_mtime,resume_available=True)
        except (OSError,ValueError):pass

    def _save(self,force=False):
        if not self._state_path or (not force and time.monotonic()-self._last_saved<1):return
        path=self._loaded_path
        if not path:return
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.tv-scan-')
        try:
            with os.fdopen(fd,'w') as handle:json.dump(self._state,handle);handle.flush();os.fsync(handle.fileno())
            os.replace(temp,path);self._last_saved=time.monotonic()
        finally:
            if os.path.exists(temp):os.unlink(temp)

    def status(self):
        with self._mutex:
            self._load()
            result = dict(self._state)
            if result.get('started'):
                result['elapsed'] = round(result.get('finished', time.time()) - result['started'], 1)
            return result

    def active(self):
        return self.status()['state'] in {'running', 'cancelling'}

    def start(self, runner):
        with self._mutex:
            if self.active():
                return self.status()
            self._cancel.clear()
            self._state = {'id': uuid.uuid4().hex, 'state': 'running', 'checked': 0, 'total': 0, 'found': 0, 'new': 0, 'errors': 0, 'current': '', 'started': time.time()}
            self._save(force=True)
            def progress(**updates):
                with self._mutex:
                    self._state.update(updates)
                    self._save(force='state' in updates)
            def work():
                try:
                    result = runner(progress, self._cancel.is_set)
                    progress(state='cancelled' if result.get('cancelled') else 'completed', resume_available=bool(result.get('cancelled')), finished=time.time(), result_count=result.get('count', 0))
                except Exception as error:
                    progress(state='failed', resume_available=True, finished=time.time(), error=str(error))
            threading.Thread(target=work, name='artflix-scan', daemon=True).start()
            return self.status()

    def cancel(self):
        with self._mutex:
            if self.active():
                self._cancel.set()
                self._state['state'] = 'cancelling'
                self._save(force=True)
            return self.status()

"""A single cancellable background library scan; the index itself lives in SQLite."""
import threading
import time
import uuid


class ScanJobs:
    def __init__(self):
        self._mutex = threading.RLock()
        self._cancel = threading.Event()
        self._state = {'state': 'idle', 'checked': 0, 'total': 0, 'found': 0, 'new': 0, 'errors': 0, 'current': '', 'elapsed': 0}

    def status(self):
        with self._mutex:
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
            def progress(**updates):
                with self._mutex:
                    self._state.update(updates)
            def work():
                try:
                    result = runner(progress, self._cancel.is_set)
                    progress(state='cancelled' if result.get('cancelled') else 'completed', finished=time.time(), result_count=result.get('count', 0))
                except Exception as error:
                    progress(state='failed', finished=time.time(), error=str(error))
            threading.Thread(target=work, name='artflix-scan', daemon=True).start()
            return self.status()

    def cancel(self):
        with self._mutex:
            if self.active():
                self._cancel.set()
                self._state['state'] = 'cancelling'
            return self.status()

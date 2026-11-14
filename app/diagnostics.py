"""Small structured application logs; never log headers, bodies or upstream URLs."""
import datetime
import json
import re
import threading
from pathlib import Path

LOCK = threading.RLock()
MAX_BYTES = 1024 * 1024


def redact(value, secrets=()):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret),'[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~-]+',r'\1[REDACTED]',text)
    text = re.sub(r'(?i)((?:api_key|client_key|tmdb_token|fanart_key|x-artflix-edit-token)[=:]\s*)[^\s&\",]+',r'\1[REDACTED]',text)
    return text


def append_event(directory: Path, event, secrets=(), **fields):
    with LOCK:
        directory.mkdir(parents=True,exist_ok=True)
        file=directory/'artflix.log'
        if file.exists() and file.stat().st_size>MAX_BYTES:
            older=directory/'artflix.log.2';older.unlink(missing_ok=True)
            previous=directory/'artflix.log.1'
            if previous.exists():previous.replace(older)
            file.replace(previous)
        record={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':str(event),**fields}
        with file.open('a') as stream:stream.write(redact(json.dumps(record,default=str),secrets)+'\n')
        file.chmod(0o600)


def recent_events(directory: Path, secrets=(), limit=200):
    records=[]
    with LOCK:
        for name in ('artflix.log.1','artflix.log'):
            path=directory/name
            if not path.is_file():continue
            for line in path.read_text(errors='replace').splitlines():
                try:records.append(json.loads(redact(line,secrets)))
                except ValueError:continue
    return records[-limit:]

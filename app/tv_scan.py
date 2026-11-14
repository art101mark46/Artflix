"""Read-only TV discovery shared by counting and episode inventory."""
import hashlib
import os
import re
from pathlib import Path

EXCLUDED={'extras','trailers','samples','featurettes','behind the scenes','deleted scenes','interviews','actors','.actors','backups'}

def directories(folder,cancelled=None):
    cancelled=cancelled or (lambda:False)
    def failed(error):raise error
    for directory,dirs,files in os.walk(folder,followlinks=False,onerror=failed):
        if cancelled():return
        dirs[:]=sorted(d for d in dirs if not (Path(directory)/d).is_symlink() and d.casefold() not in EXCLUDED and not d.startswith('.'))
        yield Path(directory),sorted(files)

def episode_candidate(path,extensions):
    return path.suffix.lower() in extensions and not re.search(r'(?i)[._-](sample|trailer)$',path.stem)

def count_and_signature(folder,extensions,cancelled=None):
    cancelled=cancelled or (lambda:False);digest=hashlib.sha256();count=0
    for directory,files in directories(folder,cancelled):
        for name in files:
            if cancelled():return None
            path=directory/name
            if path.is_symlink() or not path.is_file():continue
            stat=path.stat()
            digest.update(repr((path.relative_to(folder).as_posix(),stat.st_size,stat.st_mtime_ns)).encode())
            count+=bool(episode_candidate(path,extensions))
    return {'signature':digest.hexdigest(),'files':count} if not cancelled() else None

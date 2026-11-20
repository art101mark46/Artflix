"""Cheap, read-only change detection; never opens video contents."""
import hashlib
import os
from pathlib import Path


def signature(folder, cancelled=lambda: False):
    digest = hashlib.sha256(b'movie-scan-v1')
    def failed(error): raise error
    for directory, dirs, files in os.walk(folder, followlinks=False, onerror=failed):
        if cancelled(): return None
        dirs[:] = sorted(d for d in dirs if not (Path(directory)/d).is_symlink())
        for name in sorted(dirs + files):
            if cancelled(): return None
            path = Path(directory)/name
            info = path.lstat()
            digest.update(repr((path.relative_to(folder).as_posix(),info.st_mode,info.st_size,info.st_mtime_ns,info.st_ino)).encode('utf-8','backslashreplace'))
    return digest.hexdigest()

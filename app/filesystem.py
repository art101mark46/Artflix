"""Never replace an existing file or directory during a media rename."""
import ctypes
import errno
import os
from pathlib import Path


def rename_no_replace(source: Path, destination: Path) -> None:
    """Linux atomic no-clobber rename. Fail closed when unsupported."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError(errno.ENOSYS, 'Safe no-replace renaming is unavailable')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def rename_media_files(folder: Path, mapping: dict[str, str]) -> None:
    changes = [(folder / old, folder / new) for old, new in mapping.items() if old != new]
    for source, destination in changes:
        if source.parent != folder or destination.parent != folder or source.is_symlink() or not source.is_file():
            raise OSError(errno.EINVAL, 'Unsafe media rename path')
        if os.path.lexists(destination):
            raise FileExistsError(errno.EEXIST, 'Rename destination already exists', str(destination))
    completed = []
    try:
        for source, destination in changes:
            rename_no_replace(source, destination)
            completed.append((source, destination))
    except OSError:
        # Restore original names where possible; never overwrite a concurrently created file.
        for source, destination in reversed(completed):
            try:
                rename_no_replace(destination, source)
            except OSError:
                pass  # The file remains at destination; its contents are not deleted.
        raise

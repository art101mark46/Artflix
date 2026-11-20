"""Never replace an existing file or directory during a media rename."""
import ctypes
import errno
import os
from pathlib import Path


def _atomic_rename_no_replace(source: Path, destination: Path) -> None:
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


def rename_no_replace(source: Path, destination: Path) -> None:
    """No-clobber rename, with a hard-link fallback for regular files on FUSE.

    Linking never copies video bytes and refuses any existing destination. If
    interrupted between link and unlink, both names retain the same intact file.
    Directories require the protected atomic operation; never emulate it with an
    existence check followed by an ordinary, potentially clobbering rename.
    """
    import stat
    try:
        _atomic_rename_no_replace(source, destination)
        return
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
            raise
    if not stat.S_ISREG(source.lstat().st_mode):
        raise OSError(errno.EOPNOTSUPP,
                      'This share does not support protected folder renames. '
                      'Rename the folder manually, then scan again', str(destination))
    try:
        os.link(source, destination, follow_symlinks=False)
    except OSError as error:
        if error.errno in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM, errno.EXDEV}:
            raise OSError(errno.EOPNOTSUPP,
                          'This share supports neither protected renames nor hard links. '
                          'The original file was kept; rename it manually, then scan again',
                          str(destination)) from error
        raise
    source.unlink()


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


def publish_metadata_no_replace(source: Path, destination: Path) -> None:
    """Publish a finished metadata temp file, retaining exclusive creation on FUSE.

    This compatibility path is deliberately unavailable to videos, subtitles and
    directories. Media moves may use protected renames or hard links, but never copying.
    """
    import shutil
    import stat
    allowed={'.nfo','.jpg','.jpeg','.png','.webp'}
    if destination.suffix.lower() not in allowed and destination.name not in {'.artflix-tv.json','.artflix-assets.json'}:
        raise OSError(errno.EINVAL,'Compatibility publishing is limited to metadata and artwork')
    if source.is_symlink() or not source.is_file():raise OSError(errno.EINVAL,'Metadata source must be a regular temporary file')
    unsupported={errno.EINVAL,errno.ENOSYS,errno.EOPNOTSUPP,errno.EXDEV}
    try:
        rename_no_replace(source,destination)
        return
    except OSError as error:
        if error.errno not in unsupported:raise
    # A hard link exposes complete contents atomically and refuses existing names.
    try:
        os.link(source,destination,follow_symlinks=False)
    except OSError as error:
        if error.errno not in unsupported|{errno.EPERM}:raise
    else:
        source.unlink();return
    # Some user-share filesystems support neither operation. O_EXCL never opens
    # an existing destination (including symlinks); only new metadata is copied.
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,stat.S_IMODE(source.stat().st_mode))
    identity=os.fstat(fd)
    try:
        with os.fdopen(fd,'wb') as output,source.open('rb') as incoming:
            shutil.copyfileobj(incoming,output);output.flush();os.fsync(output.fileno())
    except BaseException:
        try:
            current=destination.lstat()
            if (current.st_dev,current.st_ino)==(identity.st_dev,identity.st_ino):destination.unlink()
        except FileNotFoundError:pass
        raise
    source.unlink()

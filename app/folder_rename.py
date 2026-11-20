"""Recoverable no-clobber folder moves for filesystems without renameat2.

Stage all files as hard links before removing any original names. Journals live
in appdata, never in video files. A pending move blocks other library writes.
"""
import errno
import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from app.filesystem import _atomic_rename_no_replace


class PendingFolderRename(OSError):
    pass


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try: os.fsync(fd)
        except OSError as error:
            if error.errno not in {errno.EINVAL, errno.EOPNOTSUPP}: raise
    finally: os.close(fd)


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.rename-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path); _sync_dir(path.parent)
    finally:
        if os.path.exists(name): os.unlink(name)


def pending(data_dir):
    directory = Path(data_dir) / 'folder-renames'
    return [dict(json.loads(p.read_text()), id=p.stem) for p in sorted(directory.glob('*.json'))]


def _tree(root):
    files, dirs = {}, ['.']
    def failed(error): raise error
    for directory, subdirs, names in os.walk(root, followlinks=False, onerror=failed):
        for name in sorted(subdirs + names):
            path = Path(directory) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode): dirs.append(relative)
            elif stat.S_ISREG(info.st_mode):
                files[relative] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
            else: raise OSError(errno.EINVAL, 'Folder contains a symlink or special file; nothing was renamed', str(path))
    return files, sorted(dirs, key=lambda d: (len(Path(d).parts), d))


def _matches(path, identity):
    try:
        s = path.lstat()
        return stat.S_ISREG(s.st_mode) and [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns] == identity
    except FileNotFoundError: return False


def _owned(data):
    source, dest = Path(data['source']), Path(data['destination'])
    if source.parent != dest.parent or source.parent.is_symlink() or source.parent.resolve() != source.parent:
        raise OSError('Rename parent mapping changed; recovery stopped')
    if source.is_symlink() or dest.is_symlink() or not dest.is_dir():
        raise OSError('Rename folder mapping changed; recovery stopped')
    marker = dest / data['marker']
    if marker.is_symlink() or marker.read_text() != data['token']:
        raise OSError('Rename ownership marker changed; recovery stopped')
    # Never follow directories replaced with symlinks during an interrupted move.
    for relative in data['dirs']:
        for root in (source, dest):
            path = root / relative
            if path.is_symlink(): raise OSError('Rename directory became a symlink; recovery stopped')
    return source, dest


def _finish(path, data):
    source, dest = _owned(data)
    if data['phase'] == 'linking':
        current, directories = _tree(source)
        if current != data['files'] or directories != data['dirs']:
            raise OSError('Source folder changed; cancel this pending rename before reviewing again')
        for relative in data['dirs'][1:]:
            target = dest / relative
            if not target.exists(): target.mkdir()
            if not target.is_dir() or target.is_symlink(): raise OSError('Destination directory changed')
        for relative, identity in data['files'].items():
            before, after = source / relative, dest / relative
            if os.path.lexists(after):
                if not _matches(before, identity) or not os.path.samefile(before, after):
                    raise FileExistsError(errno.EEXIST, 'Destination conflict; existing file preserved', str(after))
            else: os.link(before, after, follow_symlinks=False)
        # Check every source and all destination contents before committing.
        if _tree(source) != (data['files'], data['dirs']): raise OSError('Source changed while staging rename')
        destination_files, destination_dirs = _tree(dest)
        destination_files.pop(data['marker'], None)
        if set(destination_files) != set(data['files']) or destination_dirs != data['dirs']:
            raise OSError('Unexpected destination contents; rename stopped')
        for relative, identity in data['files'].items():
            if not _matches(source / relative, identity) or not os.path.samefile(source / relative, dest / relative):
                raise OSError('A staged file changed; rename stopped')
        for relative in data['dirs']:
            before, after = source / relative, dest / relative
            info = before.stat()
            if (after.stat().st_uid, after.stat().st_gid) != (info.st_uid, info.st_gid): os.chown(after, info.st_uid, info.st_gid)
            shutil.copystat(before, after, follow_symlinks=False)
        for relative in reversed(data['dirs']): _sync_dir(dest / relative)
        data['phase'] = 'committing'; _save(path, data)
    for relative, identity in data['files'].items():
        before, after = source / relative, dest / relative
        if not _matches(after, identity): raise OSError('Staged file changed; recovery stopped')
        if os.path.lexists(before):
            if not _matches(before, identity) or not os.path.samefile(before, after): raise OSError('Original file changed; recovery stopped')
            before.unlink()
    for relative in reversed(data['dirs']):
        original = source / relative
        if original.exists(): original.rmdir()  # Never remove unexpected new files.
    _sync_dir(source.parent)
    data['phase'] = 'complete'; _save(path, data)
    (dest / data['marker']).unlink()
    _sync_dir(dest)
    path.unlink(); _sync_dir(path.parent)


def recover(data_dir, key, cancel=False):
    if len(key) != 32 or any(c not in '0123456789abcdef' for c in key): raise ValueError('Invalid rename ID')
    path = Path(data_dir) / 'folder-renames' / (key + '.json')
    data = json.loads(path.read_text())
    source = Path(data['source'])
    if cancel and data['phase']=='linking' and not os.path.lexists(data['destination']):
        if _tree(source)!=(data['files'],data['dirs']): raise OSError('Original changed; cancellation stopped')
        path.unlink(); return data
    if data['phase'] == 'complete':
        # File movement finished before a restart. Only our marker may remain.
        dest = Path(data['destination']); marker = dest / data['marker']
        if source.exists() or dest.parent!=source.parent or dest.parent.resolve()!=dest.parent or dest.is_symlink() or not dest.is_dir(): raise OSError('Completed rename mapping changed')
        for relative in data['dirs']:
            if (dest/relative).is_symlink(): raise OSError('Destination directory changed')
        for relative,identity in data['files'].items():
            if not _matches(dest/relative,identity): raise OSError('Completed file changed; recovery stopped')
        if marker.exists():
            _owned(data); marker.unlink()
        path.unlink(); return data
    if cancel:
        if data['phase'] != 'linking': raise OSError('This rename has started committing; finish recovery instead')
        source, dest = _owned(data)
        for relative, identity in data['files'].items():
            if not _matches(source / relative, identity): raise OSError('Original changed; cancellation stopped')
        # Remove only links demonstrably pointing to our original file.
        for relative in data['files']:
            after = dest / relative
            if os.path.lexists(after):
                if after.is_symlink() or not os.path.samefile(source / relative, after): raise OSError('Destination conflict preserved; cancellation stopped')
                after.unlink()
        for relative in reversed(data['dirs'][1:]):
            directory = dest / relative
            if directory.exists(): directory.rmdir()
        (dest / data['marker']).unlink(); dest.rmdir(); path.unlink()
    else: _finish(path, data)
    return data


def rename_folder(source, destination, data_dir, library):
    source, destination = Path(source), Path(destination)
    if source == destination: return
    if pending(data_dir): raise PendingFolderRename('Finish or cancel the pending folder rename first in Settings → About')
    if source.parent != destination.parent or source.is_symlink() or not source.is_dir(): raise OSError('Unsafe folder rename path')
    if os.path.lexists(destination): raise FileExistsError(errno.EEXIST, 'Folder already exists', str(destination))
    try:
        _atomic_rename_no_replace(source, destination); return
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}: raise
    files, dirs = _tree(source)
    key = uuid.uuid4().hex
    path = Path(data_dir) / 'folder-renames' / (key + '.json')
    data = {'source':str(source), 'destination':str(destination), 'library':library,
            'files':files, 'dirs':dirs, 'phase':'linking', 'token':key, 'marker':'.artflix-rename-'+key}
    _save(path, data)
    try:
        try: destination.mkdir()
        except FileExistsError:
            path.unlink(); raise
        with (destination / data['marker']).open('x') as stream:
            stream.write(key); stream.flush(); os.fsync(stream.fileno())
        _finish(path, data)
    except Exception as error:
        if path.exists():
            # Before commit we can normally undo all staged links immediately.
            if data['phase'] == 'linking':
                try: recover(data_dir, key, cancel=True)
                except OSError: pass
            if path.exists(): raise PendingFolderRename('Folder rename paused safely. Open Settings → About → Folder rename recovery. '+str(error)) from error
        if data['phase']=='linking' and isinstance(error,OSError) and error.errno in {errno.EINVAL,errno.EPERM,errno.EOPNOTSUPP,errno.EXDEV,errno.ENOSYS}:
            raise OSError(error.errno, 'This share could not hard-link the files into the new folder (possibly across disks). The original folder was kept; no video data was copied') from error
        raise

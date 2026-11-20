import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.filesystem import rename_no_replace, rename_media_files


class MediaRenameFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.unsupported = patch('app.filesystem._atomic_rename_no_replace', side_effect=OSError(errno.EINVAL, 'unsupported'))
        self.unsupported.start()
        self.addCleanup(self.unsupported.stop)

    def test_video_and_subtitle_rename_without_copying(self):
        video = self.root / 'old.mkv'
        video.write_bytes(b'VIDEO')
        inode = video.stat().st_ino
        (self.root / 'old.en.srt').write_bytes(b'SUBTITLE')
        rename_media_files(self.root, {'old.mkv': 'new.mkv', 'old.en.srt': 'new.en.srt'})
        self.assertEqual((self.root / 'new.mkv').stat().st_ino, inode)
        self.assertEqual((self.root / 'new.mkv').read_bytes(), b'VIDEO')
        self.assertEqual((self.root / 'new.en.srt').read_bytes(), b'SUBTITLE')
        self.assertFalse(video.exists())

    def test_existing_destination_and_symlink_are_never_overwritten(self):
        source = self.root / 'old.mkv'
        source.write_bytes(b'VIDEO')
        dest = self.root / 'new.mkv'
        dest.write_bytes(b'OTHER')
        with self.assertRaises(FileExistsError): rename_no_replace(source, dest)
        self.assertEqual(dest.read_bytes(), b'OTHER')
        dest.unlink()
        dest.symlink_to(self.root / 'absent')
        with self.assertRaises(FileExistsError): rename_no_replace(source, dest)
        self.assertTrue(dest.is_symlink())
        self.assertEqual(source.read_bytes(), b'VIDEO')

    def test_no_hardlinks_keeps_original_and_folder_rename_fails_closed(self):
        source = self.root / 'old.mkv'
        source.write_bytes(b'VIDEO')
        with patch('app.filesystem.os.link', side_effect=OSError(errno.EPERM, 'unsupported')):
            with self.assertRaisesRegex(OSError, 'original file was kept'):
                rename_no_replace(source, self.root / 'new.mkv')
        self.assertEqual(source.read_bytes(), b'VIDEO')
        self.assertFalse((self.root / 'new.mkv').exists())
        folder = self.root / 'folder'
        folder.mkdir()
        with self.assertRaisesRegex(OSError, 'folder renames'):
            rename_no_replace(folder, self.root / 'newfolder')
        self.assertTrue(folder.is_dir())

    def test_second_file_conflict_rolls_first_back(self):
        for name in ['a.mkv', 'b.srt']: (self.root / name).write_bytes(name.encode())
        real_link = __import__('os').link
        def race(source, dest, **kwargs):
            if dest.name == 'new.srt': dest.write_bytes(b'CONCURRENT')
            return real_link(source, dest, **kwargs)
        with patch('app.filesystem.os.link', side_effect=race):
            with self.assertRaises(FileExistsError):
                rename_media_files(self.root, {'a.mkv': 'new.mkv', 'b.srt': 'new.srt'})
        self.assertEqual((self.root / 'a.mkv').read_bytes(), b'a.mkv')
        self.assertEqual((self.root / 'b.srt').read_bytes(), b'b.srt')
        self.assertEqual((self.root / 'new.srt').read_bytes(), b'CONCURRENT')
        self.assertFalse((self.root / 'new.mkv').exists())

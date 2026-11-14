import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.filesystem import publish_metadata_no_replace,rename_no_replace

class MetadataPublicationTests(unittest.TestCase):
    def test_unsupported_rename_uses_complete_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'temp';dest=Path(directory)/'actor.jpg';source.write_bytes(b'PORTRAIT')
            with patch('app.filesystem.rename_no_replace',side_effect=OSError(errno.EINVAL,'unsupported')):publish_metadata_no_replace(source,dest)
            self.assertEqual(dest.read_bytes(),b'PORTRAIT');self.assertFalse(source.exists())
    def test_unsupported_links_use_exclusive_copy_and_refuse_existing_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'temp';dest=Path(directory)/'movie.nfo';source.write_bytes(b'NFO')
            with patch('app.filesystem.rename_no_replace',side_effect=OSError(errno.EINVAL,'unsupported')),patch('app.filesystem.os.link',side_effect=OSError(errno.EOPNOTSUPP,'unsupported')):
                publish_metadata_no_replace(source,dest);self.assertEqual(dest.read_bytes(),b'NFO')
                source.write_bytes(b'OTHER')
                with self.assertRaises(FileExistsError):publish_metadata_no_replace(source,dest)
                self.assertEqual(dest.read_bytes(),b'NFO');self.assertEqual(source.read_bytes(),b'OTHER')
                dest.unlink();protected=Path(directory)/'protected.mkv';protected.write_bytes(b'VIDEO');dest.symlink_to(protected)
                with self.assertRaises(FileExistsError):publish_metadata_no_replace(source,dest)
                self.assertEqual(protected.read_bytes(),b'VIDEO')
    def test_failed_copy_cleans_only_new_metadata_and_video_fallback_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'temp';dest=Path(directory)/'actor.jpg';source.write_bytes(b'IMAGE')
            with patch('app.filesystem.rename_no_replace',side_effect=OSError(errno.EINVAL,'unsupported')),patch('app.filesystem.os.link',side_effect=OSError(errno.EOPNOTSUPP,'unsupported')),patch('shutil.copyfileobj',side_effect=OSError('write failed')):
                with self.assertRaises(OSError):publish_metadata_no_replace(source,dest)
            self.assertFalse(dest.exists());self.assertEqual(source.read_bytes(),b'IMAGE')
            with self.assertRaises(OSError):publish_metadata_no_replace(source,Path(directory)/'video.mkv')
            self.assertTrue(source.exists())

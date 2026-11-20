import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.folder_rename import rename_folder, pending, recover, PendingFolderRename


class FolderRenameTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.source=self.base/'Old';self.source.mkdir()
        self.dest=self.base/'New';self.config=self.base/'config'
        (self.source/'Season 01').mkdir();(self.source/'actors').mkdir()
        for name,data in [('movie.mkv',b'VIDEO'),('movie.en.srt',b'SUB'),('movie.nfo',b'NFO'),('actors/a.jpg',b'IMAGE'),('Season 01/ep.mkv',b'EP')]:
            (self.source/name).write_bytes(data)
        self.original={str(p.relative_to(self.source)):(p.read_bytes(),p.stat().st_ino) for p in self.source.rglob('*') if p.is_file()}
        self.atomic=patch('app.folder_rename._atomic_rename_no_replace',side_effect=OSError(errno.EINVAL,'unsupported'))
        self.atomic.start();self.addCleanup(self.atomic.stop)

    def assert_contents(self,root):
        for name,(data,inode) in self.original.items():
            self.assertEqual((root/name).read_bytes(),data)
            self.assertEqual((root/name).stat().st_ino,inode)

    def test_nested_folder_rename_keeps_file_contents_and_inodes(self):
        rename_folder(self.source,self.dest,self.config,'movies')
        self.assertFalse(self.source.exists());self.assert_contents(self.dest)
        self.assertEqual(pending(self.config),[])
        self.assertEqual({str(p.relative_to(self.dest)) for p in self.dest.rglob('*') if p.is_file()},set(self.original))

    def test_existing_folder_and_symlink_refused(self):
        self.dest.mkdir();(self.dest/'other.mkv').write_bytes(b'OTHER')
        with self.assertRaises(FileExistsError):rename_folder(self.source,self.dest,self.config,'movies')
        self.assert_contents(self.source);self.assertEqual((self.dest/'other.mkv').read_bytes(),b'OTHER')
        (self.source/'link').symlink_to(self.base/'outside')
        with self.assertRaises(OSError):rename_folder(self.source,self.base/'Different',self.config,'movies')
        self.assert_contents(self.source)

    def test_unsupported_links_roll_back_staging_with_originals_intact(self):
        real_link=os.link;calls=0
        def link(a,b,**kwargs):
            nonlocal calls
            calls+=1
            if calls==3:raise OSError(errno.EXDEV,'unsupported across disks')
            return real_link(a,b,**kwargs)
        with patch('app.folder_rename.os.link',side_effect=link):
            with self.assertRaises(OSError):rename_folder(self.source,self.dest,self.config,'movies')
        self.assert_contents(self.source);self.assertFalse(self.dest.exists());self.assertFalse(pending(self.config))

    def test_crash_during_staging_can_resume_or_cancel(self):
        real_link=os.link
        def crash(a,b,**kwargs):
            real_link(a,b,**kwargs)
            raise KeyboardInterrupt()
        with patch('app.folder_rename.os.link',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):rename_folder(self.source,self.dest,self.config,'movies')
        self.assert_contents(self.source);job=pending(self.config)[0]
        recover(self.config,job['id'],cancel=True)
        self.assertFalse(self.dest.exists());self.assert_contents(self.source)
        with patch('app.folder_rename.os.link',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):rename_folder(self.source,self.dest,self.config,'movies')
        recover(self.config,pending(self.config)[0]['id'])
        self.assertFalse(self.source.exists());self.assert_contents(self.dest)

    def test_interrupted_commit_resumes_and_cannot_be_cancelled(self):
        unlink=Path.unlink;calls=0
        def interrupt(path,*args,**kwargs):
            nonlocal calls
            if path.parent==self.source:
                calls+=1
                if calls==2:raise OSError('Interrupted')
            return unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',interrupt):
            with self.assertRaises(PendingFolderRename):rename_folder(self.source,self.dest,self.config,'movies')
        self.assert_contents(self.dest);job=pending(self.config)[0]
        self.assertEqual(job['phase'],'committing')
        with self.assertRaises(OSError):recover(self.config,job['id'],cancel=True)
        recover(self.config,job['id'])
        self.assertFalse(self.source.exists());self.assert_contents(self.dest)

    def test_concurrent_new_original_file_is_never_deleted(self):
        unlink=Path.unlink;added=False
        def add_file(path,*args,**kwargs):
            nonlocal added
            if path.parent==self.source and not added:
                added=True;(self.source/'unexpected.mkv').write_bytes(b'NEW VIDEO')
            return unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',add_file):
            with self.assertRaises(PendingFolderRename):rename_folder(self.source,self.dest,self.config,'movies')
        self.assertEqual((self.source/'unexpected.mkv').read_bytes(),b'NEW VIDEO');self.assert_contents(self.dest)
        self.assertTrue(pending(self.config))

    def test_destination_symlink_inserted_before_recovery_is_not_followed(self):
        def crash(*args,**kwargs):raise KeyboardInterrupt()
        with patch('app.folder_rename.os.link',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):rename_folder(self.source,self.dest,self.config,'movies')
        (self.dest/'actors').rmdir();outside=self.base/'outside';outside.mkdir();(self.dest/'actors').symlink_to(outside)
        with self.assertRaises(OSError):recover(self.config,pending(self.config)[0]['id'])
        self.assert_contents(self.source);self.assertEqual(list(outside.iterdir()),[])

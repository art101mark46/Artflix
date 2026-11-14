import errno
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from app.library import read_movie_metadata, preserve_unmanaged_nfo_fields
from app.filesystem import rename_media_files, rename_no_replace

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi'}


class ExistingLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / 'Film.mkv').write_bytes(b'VIDEO-CONTENTS')

    def test_filename_nfo_and_uniqueid_are_imported_without_changes(self):
        payload = b'<movie><title>Existing title</title><uniqueid type="tmdb">123</uniqueid><mpaa>15</mpaa><genre>Drama</genre><actor><name>Actor Name</name></actor><playcount>7</playcount></movie>'
        (self.folder / 'Film.nfo').write_bytes(payload)
        before = {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        result = read_movie_metadata(self.folder, VIDEO_EXTENSIONS)
        self.assertEqual(result['metadata']['tmdbid'], '123')
        self.assertEqual(result['metadata']['title'], 'Existing title')
        self.assertEqual(result['metadata']['mpaa'], '15')
        self.assertEqual(result['searchable']['actors'], ['Actor Name'])
        self.assertEqual(result['nfo_filename'], 'Film.nfo')
        self.assertNotIn('tmdb_id', result['missing_fields'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()})

    def test_complete_partial_and_absent_markers(self):
        self.assertEqual(read_movie_metadata(self.folder, VIDEO_EXTENSIONS)['metadata_status'], 'none')
        path = self.folder / 'movie.nfo'
        path.write_text('<movie><title>Film</title></movie>')
        self.assertEqual(read_movie_metadata(self.folder, VIDEO_EXTENSIONS)['metadata_status'], 'partial')
        path.write_text('<movie><title>Film</title><originaltitle>Film</originaltitle><tmdbid>1</tmdbid><year>2024</year><premiered>2024-01-01</premiered><runtime>90</runtime><genre>Drama</genre><director>A</director><credits>B</credits><studio>C</studio><plot>Plot</plot><mpaa>NR</mpaa><actor><name>D</name></actor></movie>')
        (self.folder / 'Film-poster.jpg').write_bytes(b'image')
        (self.folder / 'backdrop.png').write_bytes(b'image')
        result = read_movie_metadata(self.folder, VIDEO_EXTENSIONS)
        self.assertEqual(result['metadata_status'], 'complete')
        self.assertIn('subtitles', result['missing_fields'])
        self.assertIn('tagline', result['missing_fields'])
        self.assertIn('actor_portraits', result['missing_fields'])

    def test_movie_nfo_precedes_other_files_and_malformed_nfo_is_not_overwritten(self):
        (self.folder / 'Film.nfo').write_text('<movie><title>Filename title</title></movie>')
        (self.folder / 'movie.nfo').write_text('Unreadable NFO release notes')
        result = read_movie_metadata(self.folder, VIDEO_EXTENSIONS)
        self.assertEqual(result['nfo_filename'], 'movie.nfo')
        self.assertEqual(result['metadata_status'], 'none')
        self.assertIn('error', result['metadata'])
        self.assertEqual((self.folder / 'movie.nfo').read_text(), 'Unreadable NFO release notes')

    def test_save_preserves_unmanaged_metadata_and_external_ids(self):
        (self.folder / 'movie.nfo').write_text('<movie><title>Old</title><playcount>7</playcount><watched>true</watched><resume><position>400</position></resume><uniqueid type="imdb">tt1234</uniqueid><uniqueid type="tmdb">1</uniqueid><tag>Collection tag</tag></movie>')
        generated = b'<movie><title>New</title><uniqueid type="tmdb">2</uniqueid></movie>'
        merged = ET.fromstring(preserve_unmanaged_nfo_fields(generated, self.folder, VIDEO_EXTENSIONS))
        self.assertEqual(merged.findtext('title'), 'New')
        self.assertEqual(merged.findtext('playcount'), '7')
        self.assertEqual(merged.findtext('resume/position'), '400')
        self.assertEqual(merged.findtext('tag'), 'Collection tag')
        self.assertEqual(merged.find("uniqueid[@type='imdb']").text, 'tt1234')
        self.assertEqual(merged.find("uniqueid[@type='tmdb']").text, '2')


class NoClobberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_video_and_subtitle_renames_preserve_contents(self):
        video = self.folder / 'old.mkv'
        subtitle = self.folder / 'old.en.srt'
        video.write_bytes(b'VIDEO-EXACT')
        subtitle.write_bytes(b'SUBTITLE-EXACT')
        rename_media_files(self.folder, {'old.mkv': 'new.mkv', 'old.en.srt': 'new.en.srt'})
        self.assertEqual((self.folder / 'new.mkv').read_bytes(), b'VIDEO-EXACT')
        self.assertEqual((self.folder / 'new.en.srt').read_bytes(), b'SUBTITLE-EXACT')

    def test_file_and_folder_collisions_never_replace_existing_data(self):
        old, new = self.folder / 'old.mkv', self.folder / 'new.mkv'
        old.write_bytes(b'ORIGINAL')
        new.write_bytes(b'OTHER-MOVIE')
        with self.assertRaises(FileExistsError):
            rename_media_files(self.folder, {old.name: new.name})
        self.assertEqual(old.read_bytes(), b'ORIGINAL')
        self.assertEqual(new.read_bytes(), b'OTHER-MOVIE')
        one, two = self.folder / 'one', self.folder / 'two'
        one.mkdir()
        two.mkdir()
        with self.assertRaises(FileExistsError):
            rename_no_replace(one, two)
        self.assertTrue(one.is_dir())
        self.assertTrue(two.is_dir())

    def test_concurrent_destination_is_preserved_and_prior_move_rolled_back(self):
        (self.folder / 'a.mkv').write_bytes(b'VIDEO-A')
        (self.folder / 'b.mkv').write_bytes(b'VIDEO-B')
        real_rename = rename_no_replace
        def race(source, destination):
            if source.name == 'b.mkv':
                destination.write_bytes(b'CONCURRENT-FILE')
            real_rename(source, destination)
        with patch('app.filesystem.rename_no_replace', side_effect=race):
            with self.assertRaises(FileExistsError):
                rename_media_files(self.folder, {'a.mkv': 'new-a.mkv', 'b.mkv': 'new-b.mkv'})
        self.assertEqual((self.folder / 'a.mkv').read_bytes(), b'VIDEO-A')
        self.assertEqual((self.folder / 'b.mkv').read_bytes(), b'VIDEO-B')
        self.assertEqual((self.folder / 'new-b.mkv').read_bytes(), b'CONCURRENT-FILE')
        self.assertFalse((self.folder / 'new-a.mkv').exists())

    def test_symlink_and_traversal_renames_are_rejected(self):
        original = self.folder / 'original.mkv'
        original.write_bytes(b'VIDEO')
        (self.folder / 'link.mkv').symlink_to(original)
        with self.assertRaises(OSError):
            rename_media_files(self.folder, {'link.mkv': 'new.mkv'})
        with self.assertRaises(OSError):
            rename_media_files(self.folder, {'original.mkv': '../elsewhere.mkv'})
        self.assertEqual(original.read_bytes(), b'VIDEO')


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from app.media_roles import classify_media, selected_main_files
from app.diagnostics import append_event,recent_events


class MediaRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)/'Film';self.folder.mkdir()

    def test_main_and_conventional_extras_are_separated_without_changes(self):
        for name in ['Film.mkv','Film-trailer.mp4','Film.sample.mkv']:(self.folder/name).write_bytes(name.encode())
        (self.folder/'extras').mkdir();(self.folder/'extras'/'Interview.mkv').write_bytes(b'extra')
        before={p.relative_to(self.folder).as_posix():p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        result=classify_media(self.folder,{'.mkv','.mp4'})
        self.assertEqual([p.name for p in result['main']],['Film.mkv'])
        self.assertEqual(len(result['extras']),3)
        self.assertEqual(before,{p.relative_to(self.folder).as_posix():p.read_bytes() for p in self.folder.rglob('*') if p.is_file()})
        with self.assertRaises(ValueError):selected_main_files(self.folder,{'.mkv','.mp4'},['Film-trailer.mp4'])

    def test_multiple_candidates_need_explicit_selection(self):
        for name in ['Film.mkv','Film-Extended.mkv']:(self.folder/name).touch()
        with self.assertRaises(ValueError):selected_main_files(self.folder,{'.mkv'})
        self.assertEqual([p.name for p in selected_main_files(self.folder,{'.mkv'},['Film.mkv'])],['Film.mkv'])

    def test_possible_extra_as_only_file_is_flagged_not_silently_hidden(self):
        (self.folder/'sample.mp4').touch()
        result=classify_media(self.folder,{'.mp4'})
        self.assertEqual(result['ambiguous'],['sample.mp4'])
        self.assertEqual(len(result['main']),1)
        with self.assertRaises(ValueError):selected_main_files(self.folder,{'.mp4'})

    def test_extra_symlinks_are_not_followed(self):
        (self.folder/'Film.mkv').touch();outside=Path(self.temp.name)/'outside';outside.mkdir();(outside/'hidden.mkv').write_bytes(b'OUTSIDE')
        (self.folder/'extras').symlink_to(outside,target_is_directory=True)
        result=classify_media(self.folder,{'.mkv'})
        self.assertEqual(result['extras'],[])
        self.assertEqual((outside/'hidden.mkv').read_bytes(),b'OUTSIDE')

    def test_structured_logs_redact_keys_and_tokens(self):
        secret='private-provider-key'
        append_event(Path(self.temp.name),'failure',[secret],detail='api_key='+secret+' Bearer abcdef123')
        raw=(Path(self.temp.name)/'artflix.log').read_text()
        self.assertNotIn(secret,raw);self.assertNotIn('abcdef123',raw)
        self.assertEqual(recent_events(Path(self.temp.name))[0]['event'],'failure')


if __name__=='__main__':unittest.main()

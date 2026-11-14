import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from app.jobs import ScanJobs
from app.tv_scan import count_and_signature
from app.television import inventory

class PersistentScanTests(unittest.TestCase):
    def test_interrupted_job_recovers_without_auto_start(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'status.json'
            path.write_text(json.dumps({'state':'running','started':time.time()-10,'files_done':7,'files_total':12,'current_file':'Season 01/Show.S01E07.mkv'}))
            jobs=ScanJobs(path);status=jobs.status()
            self.assertEqual(status['state'],'interrupted');self.assertTrue(status['resume_available']);self.assertFalse(jobs.active());self.assertEqual(status['files_done'],7)
            gate=threading.Event()
            def runner(progress,cancelled):
                progress(files_done=12,files_total=12);gate.set();return {'count':1}
            jobs.start(runner);self.assertTrue(gate.wait(2))
            deadline=time.monotonic()+2
            while jobs.active() and time.monotonic()<deadline:time.sleep(.01)
            restored=ScanJobs(path).status();self.assertEqual(restored['state'],'completed');self.assertFalse(restored['resume_available']);self.assertEqual(restored['files_done'],12)
            path.write_text('{bad json');self.assertEqual(ScanJobs(path).status()['state'],'idle')

    def test_count_matches_inventory_and_excludes_extras_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ['Season 01/A.S01E01E02.mkv','Season 01/unknown.mp4','Season 01/A-trailer.mp4','extras/B.S01E03.mkv','actors/C.S01E04.mkv','Season 01/A.S01E01E02.en.srt']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'data')
            (root/'link.mkv').symlink_to(root/'Season 01/A.S01E01E02.mkv')
            before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            first=count_and_signature(root,{'.mkv','.mp4'});self.assertEqual(first['files'],2)
            events=[];inv=inventory(root,{'.mkv','.mp4'},progress=lambda **event:events.append(event))
            self.assertEqual(len(inv['episodes'])+len(inv['unrecognized']),2)
            self.assertEqual(len([e for e in events if e['phase']=='finished']),2)
            self.assertEqual(inv['episodes'][0]['sidecars'],['A.S01E01E02.en.srt'])
            self.assertEqual(before,{str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})
            (root/'tvshow.nfo').write_text('<tvshow/>');self.assertNotEqual(first['signature'],count_and_signature(root,{'.mkv','.mp4'})['signature'])

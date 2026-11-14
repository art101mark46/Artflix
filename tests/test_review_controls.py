import unittest
from pathlib import Path
from app.control import EditSessions, ReviewSigner
from app.operations import safe_metadata_relative, library_health


class ReviewControlTests(unittest.TestCase):
    def test_lock_revocation_and_independent_tabs(self):
        sessions=EditSessions()
        first,second=sessions.unlock(),sessions.unlock()
        self.assertFalse(sessions.valid(''))
        self.assertTrue(sessions.valid(first))
        sessions.lock(first)
        self.assertFalse(sessions.valid(first))
        self.assertTrue(sessions.valid(second))

    def test_approval_binds_plan_expires_and_rejects_tampering(self):
        signer=ReviewSigner();token=signer.sign('plan-one')
        self.assertTrue(signer.valid(token,'plan-one'))
        self.assertFalse(signer.valid(token,'different-plan'))
        self.assertFalse(signer.valid(token+'0','plan-one'))
        self.assertFalse(signer.valid(signer.sign('plan-one',lifetime=-1),'plan-one'))
        self.assertFalse(ReviewSigner().valid(token,'plan-one'))

    def test_restore_allowlist_excludes_video_subtitles_and_traversal(self):
        self.assertEqual(safe_metadata_relative('actors/Actor--1.jpg'),Path('actors/Actor--1.jpg'))
        for name in ['movie.mkv','movie.mp4','movie.iso','film.en.srt','../movie.nfo','/movie.nfo','actors/subfolder/image.jpg']:
            with self.subTest(name=name),self.assertRaises(ValueError):safe_metadata_relative(name)

    def test_health_groups_normalized_ids_and_counts_missing_fields(self):
        movies=[{'folder':'A','name':'A','metadata':{'tmdbid':'00123'},'missing_fields':['poster'],'metadata_status':'partial'},
                {'folder':'B','name':'B','metadata':{'tmdbid':'123'},'missing_fields':['certificate'],'metadata_status':'partial'},
                {'folder':'C','name':'C','metadata':{},'missing_fields':['poster','certificate'],'media':[{'probe_note':'failed'}],'metadata_status':'none'}]
        health=library_health(movies)
        self.assertEqual(health['duplicate_movies'],2)
        self.assertEqual(health['unmatched'],1)
        self.assertEqual(health['missing_poster'],2)
        self.assertEqual(health['missing_certificate'],2)
        self.assertEqual(health['unreadable'],1)


if __name__=='__main__':unittest.main()

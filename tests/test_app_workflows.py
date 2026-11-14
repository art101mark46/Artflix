import asyncio
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from app import main
from app.control import EditSessions, ReviewSigner
from app.jobs import ScanJobs


class AppWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'movies'
        self.root.mkdir()
        self.movie = self.root / 'Film'
        self.movie.mkdir()
        self.video = self.movie / 'Film.mkv'
        self.video.write_bytes(b'VIDEO MUST STAY EXACT')
        (self.movie / 'Film.en.srt').write_bytes(b'SUBTITLE MUST STAY EXACT')
        (self.movie / 'movie.nfo').write_text('<movie><title>Saved Film</title><tmdbid>123</tmdbid><year>2024</year><mpaa>15</mpaa><runtime>90</runtime><plot>Saved plot</plot><playcount>4</playcount><actor><name>Actor One</name><role>Character</role></actor></movie>')
        (self.movie / 'poster.jpg').write_bytes(b'OLD POSTER')
        self.config = self.base / 'config'
        self.addCleanup(patch.stopall)
        patch.multiple(main, DATA_DIR=self.config, CONFIG_PATH=self.config/'settings.json', edit_sessions=EditSessions(), review_signer=ReviewSigner(), scan_jobs=ScanJobs()).start()
        patch.dict(os.environ, {'ARTFLIX_READ_ONLY':'false'}).start()
        patch.object(main, 'probe_video', side_effect=self.fake_probe).start()
        cfg = main.load_config()
        cfg.update(root_path=str(self.root),write_path=str(self.root),region='GB')
        main.save_config(cfg)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)

    def test_movie_artwork_combines_sources_without_title_guessing(self):
        cfg=main.load_config();cfg.update(tmdb_token='SECRET',tvdb_key='TVSECRET');main.save_config(cfg)
        async def tmdb(path,params=None):
            if path=='/movie/123/images':return {'posters':[{'file_path':'/poster.jpg','iso_639_1':'en'}],'logos':[{'file_path':'/logo.png'}]}
            if path=='/movie/123/external_ids':return {'imdb_id':'tt123'}
            raise AssertionError(path)
        async def tvdb(path,params=None):
            if path=='/search':
                self.assertEqual(params,{'remote_id':'tt123','type':'movie'});return {'data':[{'type':'movie','tvdb_id':'456'}]}
            if path=='/movies/456/extended':return {'data':{'id':456,'artworks':[{'type':14,'image':'/banners/movie.jpg'}]}}
            if path=='/artwork/types':return {'data':[{'id':14,'name':'Movie Poster'}]}
            raise AssertionError(path)
        fanart={'artwork':[{'name':'poster.jpg','type':'movieposter','url':'https://assets.fanart.tv/poster.jpg'}],'notice':''}
        with patch.object(main,'fanart_movie_artwork',new=AsyncMock(return_value=fanart)),patch.object(main,'tmdb_get',side_effect=tmdb),patch.object(main.tv_services['provider'],'get',side_effect=tvdb):
            result=self.client.get('/api/artwork/123')
        self.assertEqual(result.status_code,200,result.text);art=result.json()['artwork']
        self.assertEqual(len(art),4);self.assertEqual({a['provider'] for a in art},{'fanart.tv','TMDB','TVDB'});self.assertNotIn('SECRET',result.text)
        with patch.object(main,'fanart_movie_artwork',new=AsyncMock(return_value=fanart)),patch.object(main,'tmdb_get',side_effect=tmdb),patch.object(main.tv_services['provider'],'get',new=AsyncMock(return_value={'data':[{'type':'movie','tvdb_id':'1'},{'type':'movie','tvdb_id':'2'}]})) as calls:
            result=self.client.get('/api/artwork/123');self.assertEqual(len(result.json()['artwork']),3);self.assertIn('not guessed',result.json()['notice']);self.assertEqual(calls.await_count,1)

    def test_movie_tmdb_and_tvdb_images_use_reviewed_partial_save(self):
        headers=self.unlock();payload=self.payload(metadata={},artwork_only=True,artwork={'poster.jpg':'https://image.tmdb.org/t/p/original/a.jpg','clearlogo.png':'https://artworks.thetvdb.com/banners/logo.png'})
        approved=self.approve(payload)
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'NEW')):
            result=self.client.post('/api/write',json={**approved,'selected_operations':['file:clearlogo.png']},headers=headers)
        self.assertEqual(result.status_code,200,result.text);self.assertEqual((self.movie/'poster.jpg').read_bytes(),b'OLD POSTER');self.assertEqual((self.movie/'clearlogo.png').read_bytes(),b'NEW')
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST STAY EXACT')
        bad={**payload,'artwork':{'poster.jpg':'https://image.tmdb.org.evil.test/a.jpg'}}
        self.assertEqual(self.client.post('/api/write/preview',json=bad).status_code,400)

    def fake_probe(self,path):
        return {'name':path.name,'size_bytes':path.stat().st_size,'duration':5400,'video':[{'codec':'h264','width':1920,'height':1080,'aspect_ratio':'16:9','scan_type':'Progressive'}],'audio':[{'codec':'ac3','channels':6,'language':'eng'}],'subtitles':[]}

    def unlock(self):
        response = self.client.post('/api/edit-lock',json={'locked':False})
        self.assertEqual(response.status_code,200)
        return {'X-Artflix-Edit-Token':response.json()['token']}

    def payload(self,**extra):
        return {'folder':'Film','tmdb_id':123,'metadata':{'title':'Changed Film'},'replace_existing':True,**extra}

    def approve(self,payload):
        preview = self.client.post('/api/write/preview',json=payload)
        self.assertEqual(preview.status_code,200,preview.text)
        return {**payload,'review_token':preview.json()['review_token']}

    def save(self,payload,headers=None):
        result = self.client.post('/api/write',json=self.approve(payload),headers=headers or self.unlock())
        self.assertEqual(result.status_code,200,result.text)
        return result

    def test_movie_view_is_local_and_scan_preserves_media(self):
        before = {p.name:p.read_bytes() for p in self.movie.iterdir()}
        with patch.object(main,'tmdb_get',new=AsyncMock(side_effect=AssertionError('unexpected provider lookup'))):
            self.assertEqual(self.client.get('/api/scan').status_code,200)
            response = self.client.post('/api/inspect',json={'folder':'Film'})
            self.assertEqual(response.status_code,200,response.text)
            local = response.json()['local']
            self.assertEqual(local['movie']['details']['title'],'Saved Film')
            self.assertIn('/api/local-image?',local['movie']['details']['poster_url'])
            self.assertTrue(local['artwork']['artwork'][0]['local'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.movie.iterdir()})
        library = self.client.get('/api/library').json()
        self.assertEqual(library['count'],1)
        self.assertEqual(library['movies'][0]['metadata']['tmdbid'],'123')

    def test_padlock_blocks_changes_and_approved_write_works_without_provider(self):
        payload = self.payload()
        self.assertEqual(self.client.post('/api/write',json=payload).status_code,423)
        headers = self.unlock()
        self.assertEqual(self.client.post('/api/write',json=payload,headers=headers).status_code,409)
        with patch.object(main,'tmdb_get',new=AsyncMock(side_effect=AssertionError('unexpected provider lookup'))):
            self.save(payload,headers)
        self.assertIn('Changed Film',(self.movie/'movie.nfo').read_text())
        self.assertIn('<playcount>4</playcount>',(self.movie/'movie.nfo').read_text())
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST STAY EXACT')
        self.assertEqual(self.client.get('/api/library').json()['movies'][0]['metadata']['title'],'Changed Film')

    def test_review_is_invalidated_by_external_file_change(self):
        headers = self.unlock()
        reviewed = self.approve(self.payload())
        content = '<movie><title>External edit</title><tmdbid>123</tmdbid></movie>'
        (self.movie/'movie.nfo').write_text(content)
        response = self.client.post('/api/write',json=reviewed,headers=headers)
        self.assertEqual(response.status_code,409)
        self.assertEqual((self.movie/'movie.nfo').read_text(),content)

    def test_relocking_revokes_token_and_readonly_overrides_unlock(self):
        headers = self.unlock()
        reviewed = self.approve(self.payload())
        self.client.post('/api/edit-lock',json={'locked':True},headers=headers)
        self.assertEqual(self.client.post('/api/write',json=reviewed,headers=headers).status_code,423)
        headers = self.unlock()
        with patch.dict(os.environ,{'ARTFLIX_READ_ONLY':'true'}):
            self.assertEqual(self.client.post('/api/write',json=reviewed,headers=headers).status_code,403)
            self.assertEqual(self.client.post('/api/reset',json={'folder':'Film','confirm':True,'expected_files':[]},headers=headers).status_code,403)
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST STAY EXACT')

    def test_settings_changes_require_unlock(self):
        settings = main.load_config()
        self.assertEqual(self.client.put('/api/settings',json=settings).status_code,423)
        self.assertEqual(self.client.put('/api/settings',json=settings,headers=self.unlock()).status_code,200)

    def test_backup_restore_requires_review_and_preserves_current_version(self):
        original = (self.movie/'movie.nfo').read_bytes()
        headers = self.unlock()
        self.save(self.payload(),headers)
        backups = self.client.get('/api/backups',params={'folder':'Film'}).json()['backups']
        self.assertEqual(len(backups),1)
        payload = {'folder':'Film','backup_id':backups[0]['id']}
        self.assertEqual(self.client.post('/api/backups/restore',json=payload,headers=headers).status_code,409)
        preview = self.client.post('/api/backups/preview',json=payload)
        self.assertEqual(preview.status_code,200,preview.text)
        payload['review_token']=preview.json()['review_token']
        response = self.client.post('/api/backups/restore',json=payload,headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((self.movie/'movie.nfo').read_bytes(),original)
        self.assertEqual(len(self.client.get('/api/backups',params={'folder':'Film'}).json()['backups']),2)
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST STAY EXACT')
        self.assertEqual(self.client.post('/api/backups/preview',json={'folder':'Film','backup_id':'../video.mkv'}).status_code,400)

    def test_reset_is_locked_and_backs_up_only_metadata(self):
        plan=self.client.post('/api/reset',json={'folder':'Film'}).json()
        payload={'folder':'Film','confirm':True,'expected_files':plan['files']}
        self.assertEqual(self.client.post('/api/reset',json=payload).status_code,423)
        response=self.client.post('/api/reset',json=payload,headers=self.unlock())
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST STAY EXACT')
        self.assertEqual((self.movie/'Film.en.srt').read_bytes(),b'SUBTITLE MUST STAY EXACT')
        self.assertFalse((self.movie/'movie.nfo').exists())
        self.assertEqual(len(self.client.get('/api/backups',params={'folder':'Film'}).json()['backups']),2)
        self.assertEqual(self.client.get('/api/library').json()['count'],1)

    def test_single_artwork_save_is_reviewed_and_source_remembered(self):
        payload=self.payload(metadata={},artwork_only=True,artwork={'poster.jpg':'https://assets.fanart.tv/fanart/poster.jpg'})
        headers=self.unlock()
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'NEW POSTER')):
            self.save(payload,headers)
        self.assertEqual((self.movie/'poster.jpg').read_bytes(),b'NEW POSTER')
        self.assertEqual(json.loads((self.movie/'.artflix-assets.json').read_text())['artwork']['poster.jpg'],payload['artwork']['poster.jpg'])
        self.assertIn('Saved Film',(self.movie/'movie.nfo').read_text())

    def test_relocking_during_download_blocks_commit(self):
        headers=self.unlock()
        payload=self.approve(self.payload(metadata={},artwork_only=True,artwork={'poster.jpg':'https://assets.fanart.tv/fanart/poster.jpg'}))
        async def download_and_lock(*args):
            main.edit_sessions.lock(headers['X-Artflix-Edit-Token'])
            return b'NEW POSTER'
        with patch.object(main,'download_image',new=download_and_lock):
            result=self.client.post('/api/write',json=payload,headers=headers)
        self.assertEqual(result.status_code,423)
        self.assertEqual((self.movie/'poster.jpg').read_bytes(),b'OLD POSTER')
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST STAY EXACT')

    def test_external_edit_during_download_requires_fresh_review(self):
        headers=self.unlock()
        payload=self.approve(self.payload(metadata={},artwork_only=True,artwork={'poster.jpg':'https://assets.fanart.tv/fanart/poster.jpg'}))
        changed='<movie><title>Edited while downloading</title><tmdbid>123</tmdbid></movie>'
        async def download_and_change(*args):
            (self.movie/'movie.nfo').write_text(changed)
            return b'NEW POSTER'
        with patch.object(main,'download_image',new=download_and_change):
            result=self.client.post('/api/write',json=payload,headers=headers)
        self.assertEqual(result.status_code,409)
        self.assertEqual((self.movie/'poster.jpg').read_bytes(),b'OLD POSTER')
        self.assertEqual((self.movie/'movie.nfo').read_text(),changed)

    def test_health_and_duplicate_ids_do_not_delete_or_merge_anything(self):
        other=self.root/'Another Edition';other.mkdir();(other/'Film.mp4').write_bytes(b'OTHER VIDEO');(other/'movie.nfo').write_text('<movie><title>Other edition</title><tmdbid>123</tmdbid></movie>')
        self.client.get('/api/scan')
        health=self.client.get('/api/library/health').json()
        self.assertEqual(health['total'],2)
        self.assertEqual(health['duplicate_movies'],2)
        self.assertEqual(health['duplicates'][0]['tmdb_id'],'123')
        self.assertEqual(health['missing_certificate'],1)
        self.assertEqual((other/'Film.mp4').read_bytes(),b'OTHER VIDEO')

    def test_backup_remains_available_after_approved_folder_rename(self):
        headers=self.unlock()
        response=self.save(self.payload(folder_name='Renamed Film'),headers)
        self.assertEqual(response.json()['folder'],'Renamed Film')
        self.assertFalse(self.movie.exists())
        self.assertEqual((self.root/'Renamed Film'/'Film.mkv').read_bytes(),b'VIDEO MUST STAY EXACT')
        backups=self.client.get('/api/backups',params={'folder':'Renamed Film'}).json()['backups']
        self.assertEqual(len(backups),1)
        self.assertEqual(self.client.post('/api/backups/preview',json={'folder':'Renamed Film','backup_id':backups[0]['id']}).status_code,200)

    def test_scan_counts_unreadable_video_without_changing_it(self):
        def unreadable(path):
            return {**self.fake_probe(path),'probe_note':'Unable to read video'}
        with patch.object(main,'probe_video',side_effect=unreadable):
            result=main.scan_library()
        self.assertEqual(len(result['warnings']),1)
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST STAY EXACT')

    def test_extras_and_their_subtitles_are_never_in_main_rename_plan(self):
        for name in ['Film-trailer.mp4','Film.sample.mkv','Film.sample.en.srt']:(self.movie/name).write_bytes(('EXTRA '+name).encode())
        (self.movie/'extras').mkdir();(self.movie/'extras'/'Bonus.mkv').write_bytes(b'BONUS')
        response=self.client.post('/api/inspect',json={'folder':'Film'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['videos']),1)
        self.assertEqual(len(response.json()['extras']),3)
        payload=self.payload(rename_videos=True,primary_videos=['Film.mkv'])
        plan=self.client.post('/api/write/preview',json=payload)
        self.assertEqual(plan.status_code,200,plan.text)
        names={item['before'] for item in plan.json()['renames']}
        self.assertEqual(names,{'Film.mkv','Film.en.srt'})
        self.save(payload)
        self.assertEqual((self.movie/'Film-trailer.mp4').read_bytes(),b'EXTRA Film-trailer.mp4')
        self.assertEqual((self.movie/'Film.sample.en.srt').read_bytes(),b'EXTRA Film.sample.en.srt')
        self.assertEqual((self.movie/'extras'/'Bonus.mkv').read_bytes(),b'BONUS')
        self.assertEqual((self.movie/'Changed Film (2024).mkv').read_bytes(),b'VIDEO MUST STAY EXACT')

    def test_ambiguous_video_selection_is_enforced_on_the_server(self):
        (self.movie/'Extended.mkv').write_bytes(b'EXTENDED')
        self.assertEqual(self.client.post('/api/write/preview',json=self.payload(rename_videos=True)).status_code,409)
        self.assertEqual(self.client.post('/api/write/preview',json=self.payload(rename_videos=True,primary_videos=['../outside.mkv'])).status_code,409)
        self.assertEqual(self.client.post('/api/write/preview',json=self.payload(rename_videos=True,primary_videos=['Film.mkv'])).status_code,200)
        self.assertEqual((self.movie/'Extended.mkv').read_bytes(),b'EXTENDED')

    def test_saved_searches_persist_and_can_be_archived_and_restored(self):
        payload={'name':'My missing subtitles','data':{'filters':{'resolution':'4k'},'missing_fields':['subtitles'],'missing_mode':'all'}}
        self.assertEqual(self.client.post('/api/saved-searches',json=payload).status_code,423)
        headers=self.unlock()
        with patch.dict(os.environ,{'ARTFLIX_READ_ONLY':'true'}):
            response=self.client.post('/api/saved-searches',json=payload,headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        identifier=response.json()['id']
        listing=self.client.get('/api/saved-searches').json()
        self.assertEqual(listing['searches'][0]['data']['missing_fields'],['subtitles'])
        self.assertGreaterEqual(len(listing['builtins']),4)
        self.assertEqual(self.client.delete('/api/saved-searches/'+identifier,headers=headers).status_code,200)
        listing=self.client.get('/api/saved-searches').json();self.assertEqual(listing['searches'],[]);self.assertEqual(len(listing['archived']),1)
        self.assertEqual(self.client.post('/api/saved-searches',json={**payload,'id':identifier},headers=headers).status_code,200)
        self.assertEqual(len(self.client.get('/api/saved-searches').json()['searches']),1)
        self.assertEqual(self.client.post('/api/saved-searches',json={'name':'Invalid','data':{'filters':{'api_key':'unsafe'}}},headers=headers).status_code,400)

    def test_collection_completeness_distinguishes_owned_missing_upcoming_and_unknown(self):
        self.client.get('/api/scan')
        original=self.video.read_bytes()
        async def provider(path,params=None):
            if path=='/movie/123':return {'belongs_to_collection':{'id':9,'name':'Example Saga'}}
            if path=='/collection/9':return {'id':9,'name':'Example Saga','parts':[{'id':123,'title':'Owned','release_date':'2020-01-01'},{'id':124,'title':'Missing','release_date':'2021-01-01'},{'id':125,'title':'Upcoming','release_date':'9999-01-01'},{'id':126,'title':'Unknown','release_date':''}]}
            raise AssertionError(path)
        mocked=AsyncMock(side_effect=provider)
        with patch.object(main,'tmdb_get',new=mocked):
            response=self.client.get('/api/collections/completeness',params={'movie_id':123})
            self.assertEqual(response.status_code,200,response.text)
            result=response.json();self.assertEqual((result['owned'],result['missing'],result['upcoming'],result['unknown_date']),(1,1,1,1))
            self.client.get('/api/collections/completeness',params={'movie_id':123});self.assertEqual(mocked.await_count,2)
            self.client.get('/api/collections/completeness',params={'movie_id':123,'refresh':True});self.assertEqual(mocked.await_count,4)
        self.assertEqual(self.video.read_bytes(),original)

    def test_deployment_checks_do_not_write_to_media_and_diagnostics_redact_secrets(self):
        cfg=main.load_config();cfg.update(tmdb_token='TOP-SECRET-TMDB',fanart_key='TOP-SECRET-FANART');main.save_config(cfg)
        before={p.name:p.read_bytes() for p in self.movie.iterdir()}
        result=self.client.get('/api/deployment/checks');self.assertEqual(result.status_code,200,result.text)
        checks={c['key']:c for c in result.json()['checks']};self.assertEqual(checks['read_path']['status'],'pass');self.assertEqual(checks['database']['status'],'pass')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.movie.iterdir()})
        main.journal('redaction.check',detail='TOP-SECRET-TMDB api_key=TOP-SECRET-FANART')
        self.client.post('/api/write',json=self.payload())
        report=self.client.get('/api/diagnostics/report');self.assertEqual(report.status_code,200,report.text)
        self.assertIn('attachment;',report.headers['content-disposition'])
        self.assertEqual(report.json()['system']['version'],main.VERSION)
        self.assertNotIn('TOP-SECRET-TMDB',report.text);self.assertNotIn('TOP-SECRET-FANART',report.text)
        self.assertNotIn(str(self.root),report.text)
        self.assertTrue(report.json()['events'])
        cfg['root_path']=str(self.base/'missing');main.save_config(cfg)
        checks={c['key']:c for c in self.client.get('/api/deployment/checks').json()['checks']}
        self.assertEqual(checks['read_path']['status'],'fail')

    def test_background_scan_cancellation_retains_previous_index(self):
        self.client.get('/api/scan')
        extra=self.root/'New Film';extra.mkdir();(extra/'New.mkv').write_bytes(b'NEW VIDEO')
        entered,release=threading.Event(),threading.Event()
        def delayed(path):
            if path.name=='New.mkv':entered.set();release.wait(3)
            return self.fake_probe(path)
        with patch.object(main,'probe_video',side_effect=delayed):
            response=self.client.post('/api/scan/start')
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(entered.wait(2))
            status=self.client.get('/api/scan/status').json();self.assertEqual(status['state'],'running');self.assertGreaterEqual(status['total'],2)
            self.assertEqual(self.client.post('/api/scan/cancel').status_code,200)
            release.set()
            deadline=time.time()+3
            while main.scan_jobs.active() and time.time()<deadline:time.sleep(.01)
        self.assertEqual(main.scan_jobs.status()['state'],'cancelled')
        self.assertEqual(self.client.get('/api/library').json()['count'],1)
        self.assertTrue((extra/'New.mkv').exists())

    def test_movie_review_subset_writes_only_selected_file(self):
        before=(self.movie/'movie.nfo').read_bytes();payload=self.payload(artwork={'poster.jpg':'https://assets.fanart.tv/test.jpg'})
        approved=self.approve(payload)
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'NEW POSTER')):
            r=self.client.post('/api/write',json={**approved,'selected_operations':['file:poster.jpg']},headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual((self.movie/'movie.nfo').read_bytes(),before);self.assertEqual((self.movie/'poster.jpg').read_bytes(),b'NEW POSTER')
        r=self.client.post('/api/write',json={**self.approve(payload),'selected_operations':['file:video.mkv']},headers=self.unlock());self.assertEqual(r.status_code,400)


if __name__=='__main__':unittest.main()

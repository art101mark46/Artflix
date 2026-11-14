import json
import os
import tempfile
import unittest
import httpx
from pathlib import Path
from unittest.mock import AsyncMock,patch,Mock

from fastapi.testclient import TestClient
from app import main
from app.control import EditSessions,ReviewSigner
from app.television import episode_numbers,completeness,inventory,serialize_nfo,read_nfo,TVDB,normalize_credits,tv_cast,tvdb_image_url


class TelevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'TV';self.root.mkdir();self.show=self.root/'Example Show';self.show.mkdir();self.season=self.show/'Season 01';self.season.mkdir()
        self.video=self.season/'Show.S01E01.mkv';self.video.write_bytes(b'VIDEO MUST NEVER CHANGE');(self.season/'Show.S01E01.en.srt').write_bytes(b'SUBTITLE')
        (self.show/'tvshow.nfo').write_text('<tvshow><title>Example Show</title><tvdbid>123</tvdbid><year>2024</year><mpaa>15</mpaa><plot>Local plot</plot><playcount>8</playcount><uniqueid type="imdb">tt123</uniqueid></tvshow>')
        self.config=self.base/'config';self.addCleanup(patch.stopall)
        patch.multiple(main,DATA_DIR=self.config,CONFIG_PATH=self.config/'settings.json',edit_sessions=EditSessions(),review_signer=ReviewSigner()).start()
        patch.dict(os.environ,{'ARTFLIX_READ_ONLY':'false'}).start()
        patch.object(main,'probe_video',side_effect=lambda p:{'name':p.name,'size_bytes':p.stat().st_size,'duration':60,'video':[{'codec':'h264','width':1920,'height':1080}],'audio':[],'subtitles':[]}).start()
        movie=self.base/'Movies';movie.mkdir();cfg=main.load_config();cfg.update(root_path=str(movie),write_path=str(movie),tv_root_path=str(self.root),tv_write_path=str(self.root),tvdb_key='TV-SECRET',tvdb_pin='PIN-SECRET');main.save_config(cfg)
        self.bundle={'details':{'id':123,'name':'Provider Show','characters':[]},'episodes':[{'id':1,'seasonNumber':1,'number':1,'name':'Pilot','aired':'2024-01-01'},{'id':2,'seasonNumber':1,'number':2,'name':'Second','aired':'2024-01-02'},{'id':3,'seasonNumber':1,'number':3,'name':'Future','aired':'9999-01-01'},{'id':4,'seasonNumber':0,'number':1,'name':'Special','aired':'2024-01-01'}],'order':'official','language':'eng','updated':1}
        main.cache_provider('tv-series:123:official:eng',self.bundle)
        self.client=TestClient(main.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
    def unlock(self):return {'X-Artflix-Edit-Token':self.client.post('/api/edit-lock',json={'locked':False}).json()['token']}
    def payload(self,**kw):return {'folder':self.show.name,'tvdb_id':123,'metadata':{'title':'Example Show'},'replace_existing':True,**kw}
    def approve(self,payload):
        r=self.client.post('/api/tv/write/preview',json=payload);self.assertEqual(r.status_code,200,r.text);return {**payload,'review_token':r.json()['review_token']}
    def test_separate_tv_settings_and_combined_format(self):
        headers=self.unlock();cfg=self.client.get('/api/settings').json()
        cfg.update(tv_sort_ignore_prefixes=['Le','La'],sort_ignore_prefixes=['The'],tv_filename_format='{title} S{season}E{episode}',tv_multi_episode_filename_format='{show} S{season}E{episode}')
        response=self.client.put('/api/settings',json=cfg,headers=headers);self.assertEqual(response.status_code,200,response.text)
        saved=self.client.get('/api/settings').json();self.assertEqual(saved['tv_sort_ignore_prefixes'],['Le','La']);self.assertEqual(saved['sort_ignore_prefixes'],['The'])
        combined=self.season/'Show.S01E01E02.mkv';combined.write_bytes(b'COMBINED')
        payload={'folder':self.show.name,'video':'Season 01/'+combined.name,'tvdb_id':123,'title':'Example Show','filename_format':'{title} S{season}E{episode}'}
        result=self.client.post('/api/tv/rename-example',json=payload);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['after'],'Season 01/Example Show S01E01E02.mkv')
        plan=self.client.post('/api/tv/write/preview',json=self.payload(rename_episodes=True,filename_format=cfg['tv_filename_format']))
        self.assertEqual(plan.status_code,200,plan.text);self.assertIn({'before':payload['video'],'after':result.json()['after']},plan.json()['renames'])
        cfg['tv_multi_episode_filename_format']='../{show}'
        self.assertEqual(self.client.put('/api/settings',json=cfg,headers=headers).status_code,400)
        self.assertEqual(combined.read_bytes(),b'COMBINED')

    def test_multi_provider_tv_artwork_and_safe_identity(self):
        cfg=main.load_config();cfg.update(tmdb_token='SECRET',fanart_key='FANART-SECRET');main.save_config(cfg)
        async def tvdb(path,params=None):
            if path=='/series/123/extended':return {'data':{'id':123,'artworks':[{'type':2,'image':'/banners/test.jpg','language':'eng'},{'type':2,'image':'https://evil.test/image.jpg'}]}}
            if path=='/artwork/types':return {'data':[{'id':2,'name':'Series Poster'}]}
            raise AssertionError(path)
        async def tmdb(path,params=None):
            if path=='/find/123':
                self.assertEqual(params['external_source'],'tvdb_id');return {'tv_results':[{'id':456}]}
            if path=='/tv/456/images':return {'posters':[{'file_path':'/poster.jpg','iso_639_1':'en'},{'file_path':'https://evil.test/x.jpg'}],'backdrops':[{'file_path':'/background.jpg'}]}
            raise AssertionError(path)
        client=AsyncMock();client.get.return_value=httpx.Response(200,json={'tvposter':[{'url':'https://assets.fanart.tv/poster.jpg','lang':'en'}]})
        with patch('app.tv_routes.httpx.AsyncClient') as factory,patch.object(main.tv_services['provider'],'get',side_effect=tvdb),patch.object(main,'tmdb_get',side_effect=tmdb):
            factory.return_value.__aenter__.return_value=client
            response=self.client.get('/api/tv/artwork/123',params={'folder':self.show.name})
        self.assertEqual(response.status_code,200,response.text);art=response.json()['artwork']
        self.assertEqual({a['provider'] for a in art},{'TVDB','TMDB','fanart.tv'});self.assertEqual(len(art),4)
        self.assertNotIn('SECRET',response.text);self.assertNotIn('evil.test',response.text)
        cfg['fanart_key']='';main.save_config(cfg)
        with patch.object(main.tv_services['provider'],'get',new=AsyncMock(side_effect=main.HTTPException(502,'provider failed'))),patch.object(main,'tmdb_get',new=AsyncMock(return_value={'tv_results':[{'id':1},{'id':2}]})) as calls:
            response=self.client.get('/api/tv/artwork/123');self.assertEqual(response.status_code,200);self.assertIn('not guessed',response.json()['notice']);self.assertEqual(calls.await_count,1)

    def test_provider_images_use_signed_partial_save_and_display_preference(self):
        headers=self.unlock();payload=self.payload(artwork_only=True,metadata={},artwork={'poster.jpg':'https://image.tmdb.org/t/p/original/poster.jpg','fanart.jpg':'https://artworks.thetvdb.com/banners/background.jpg'})
        reviewed=self.approve(payload)
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'IMAGE')):
            response=self.client.post('/api/tv/write',json={**reviewed,'selected_operations':['file:poster.jpg']},headers=headers)
        self.assertEqual(response.status_code,200,response.text);self.assertTrue((self.show/'poster.jpg').exists());self.assertFalse((self.show/'fanart.jpg').exists())
        choice={'folder':self.show.name,'name':'poster.jpg'}
        self.assertEqual(self.client.post('/api/tv/display-image',json=choice).status_code,423)
        self.assertEqual(self.client.post('/api/tv/display-image',json=choice,headers=headers).status_code,200)
        self.assertEqual(self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json()['display_image'],'poster.jpg')
        self.assertEqual(self.client.post('/api/tv/display-image',json={**choice,'name':'../video.mkv'},headers=headers).status_code,400)
        bad={**payload,'artwork':{'poster.jpg':'https://image.tmdb.org.evil.test/a.jpg'}}
        self.assertEqual(self.client.post('/api/tv/write/preview',json=bad).status_code,400)
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST NEVER CHANGE')

    def test_scan_reports_files_and_reuses_unchanged_shows(self):
        events=[];calls=[]
        def probe(path):calls.append(path.name);return {'name':path.name,'size_bytes':path.stat().st_size,'video':[],'audio':[],'subtitles':[]}
        before={str(p):p.read_bytes() for p in self.show.rglob('*') if p.is_file()}
        with patch.object(main,'probe_video',side_effect=probe):
            main.tv_services['scan'](lambda **kw:events.append(kw))
            self.assertEqual(len(calls),1);self.assertTrue(any(e.get('current_file')=='Season 01/Show.S01E01.mkv' for e in events));self.assertTrue(any(e.get('files_total')==1 for e in events))
            events.clear();main.tv_services['scan'](lambda **kw:events.append(kw));self.assertEqual(len(calls),1);self.assertTrue(any(e.get('reused_shows')==1 for e in events))
            self.video.write_bytes(b'NEW FILE CONTENT');main.tv_services['scan']();self.assertEqual(len(calls),2)
        self.assertEqual(before[str(self.video)],b'VIDEO MUST NEVER CHANGE')
        self.assertEqual((self.show/'tvshow.nfo').read_bytes(),before[str(self.show/'tvshow.nfo')])

    def test_cancel_mid_show_retains_completed_probes_for_resume(self):
        second=self.season/'Show.S01E02.mkv';second.write_bytes(b'SECOND VIDEO')
        cancelled=False;calls=[]
        def progress(**kw):
            nonlocal cancelled
            if kw.get('files_done')==1:cancelled=True
        def probe(path):calls.append(path.name);return {'name':path.name,'video':[],'audio':[],'subtitles':[]}
        with patch.object(main,'probe_video',side_effect=probe):
            result=main.tv_services['scan'](progress,lambda:cancelled);self.assertTrue(result['cancelled']);self.assertEqual(len(calls),1)
            self.assertEqual(self.client.get('/api/tv/library').json()['shows'],[])
            events=[];main.tv_services['scan'](lambda **kw:events.append(kw));self.assertEqual(len(calls),2)
        self.assertEqual(len(self.client.get('/api/tv/library').json()['shows']),1)
        self.assertTrue(any(e.get('show_files_done')==2 for e in events));self.assertEqual(second.read_bytes(),b'SECOND VIDEO')

    def test_failed_probe_is_retried_and_not_checkpointed_as_unchanged(self):
        with patch.object(main,'probe_video',new=Mock(side_effect=[{'probe_note':'Temporary timeout','name':self.video.name},{'name':self.video.name,'video':[],'audio':[],'subtitles':[]}])) as probe:
            first=main.tv_services['scan']();self.assertTrue(first['warnings'])
            second=main.tv_services['scan']();self.assertEqual(probe.call_count,2);self.assertEqual(second['warnings'],[])

    def test_resume_skips_completed_folder_but_detects_metadata_changes(self):
        other=self.root/'Z Other';other.mkdir();(other/'Other.S01E01.mkv').write_bytes(b'OTHER VIDEO')
        stop=False;events=[]
        def progress(**kw):
            nonlocal stop
            if kw.get('checked')==1:stop=True
        with patch.object(main,'probe_video',side_effect=lambda path:{'name':path.name,'video':[],'audio':[],'subtitles':[]}) as probe:
            self.assertTrue(main.tv_services['scan'](progress,lambda:stop)['cancelled']);self.assertEqual(probe.call_count,1)
            main.tv_services['scan'](lambda **kw:events.append(kw));self.assertEqual(probe.call_count,2)
            self.assertTrue(any(e.get('reused_shows')==1 for e in events));self.assertEqual(len(self.client.get('/api/tv/library').json()['shows']),2)
            nfo=self.show/'tvshow.nfo';nfo.write_text(nfo.read_text().replace('Local plot','New local plot'))
            main.tv_services['scan']();self.assertEqual(probe.call_count,2)
            shows=self.client.get('/api/tv/library').json()['shows'];self.assertEqual(next(s for s in shows if s['folder']==self.show.name)['metadata']['plot'],'New local plot')

    def test_scan_local_persistent_and_separate_from_movies(self):
        before={p.relative_to(self.show).as_posix():p.read_bytes() for p in self.show.rglob('*') if p.is_file()}
        with patch.object(main.tv_services['provider'],'get',new=AsyncMock(side_effect=AssertionError('Unexpected network'))):
            main.tv_services['scan']();data=self.client.get('/api/tv/library').json();inspected=self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json()
        self.assertEqual(len(data['shows']),1);self.assertEqual(self.client.get('/api/library').json()['count'],0)
        self.assertEqual(inspected['completeness']['counts']['missing'],1);self.assertEqual(inspected['completeness']['counts']['upcoming'],1);self.assertEqual(inspected['completeness']['counts']['specials'],1)
        self.assertEqual(before,{p.relative_to(self.show).as_posix():p.read_bytes() for p in self.show.rglob('*') if p.is_file()})
        self.assertEqual(len(self.client.get('/api/tv/library').json()['shows']),1)
    def test_settings_and_diagnostics_never_return_tv_credentials(self):
        text=self.client.get('/api/settings').text+self.client.get('/api/diagnostics/report').text
        self.assertNotIn('TV-SECRET',text);self.assertNotIn('PIN-SECRET',text)
        self.assertTrue(self.client.get('/api/settings').json()['has_tvdb_key'])
    def test_lock_review_and_readonly_guards(self):
        payload=self.payload();self.assertEqual(self.client.post('/api/tv/write',json=payload).status_code,423)
        headers=self.unlock();self.assertEqual(self.client.post('/api/tv/write',json=payload,headers=headers).status_code,409)
        approved=self.approve(payload)
        with patch.dict(os.environ,{'ARTFLIX_READ_ONLY':'true'}):self.assertEqual(self.client.post('/api/tv/write',json=approved,headers=headers).status_code,403)
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST NEVER CHANGE')
    def test_save_preserves_watched_and_other_ids_and_generates_episode_nfo(self):
        payload=self.approve(self.payload(episode_nfos=True,metadata={'title':'New Title','certificate':'NR'}))
        response=self.client.post('/api/tv/write',json=payload,headers=self.unlock());self.assertEqual(response.status_code,200,response.text)
        nfo=(self.show/'tvshow.nfo').read_text();self.assertIn('<playcount>8</playcount>',nfo);self.assertIn('tt123',nfo);self.assertIn('<mpaa>NR</mpaa>',nfo)
        self.assertIn('<episode>1</episode>',self.video.with_suffix('.nfo').read_text());self.assertEqual(self.video.read_bytes(),b'VIDEO MUST NEVER CHANGE')
    def test_external_changes_invalidate_review(self):
        payload=self.approve(self.payload());self.video.write_bytes(b'EXTERNALLY CHANGED VIDEO')
        response=self.client.post('/api/tv/write',json=payload,headers=self.unlock());self.assertEqual(response.status_code,409)
    def test_episode_rename_includes_subtitles_and_preserves_extras(self):
        extra=self.season/'Show.S01E01-trailer.mkv';extra.write_bytes(b'TRAILER')
        (self.season/'Show.S01E01.nfo').write_text('<episodedetails><title>Old</title><playcount>5</playcount></episodedetails>')
        payload=self.approve(self.payload(rename_episodes=True,episode_nfos=True))
        response=self.client.post('/api/tv/write',json=payload,headers=self.unlock());self.assertEqual(response.status_code,200,response.text)
        dest=self.season/'Example Show - S01E01 - Pilot.mkv';self.assertEqual(dest.read_bytes(),b'VIDEO MUST NEVER CHANGE');self.assertEqual(dest.with_suffix('.en.srt').read_bytes(),b'SUBTITLE');self.assertEqual(extra.read_bytes(),b'TRAILER');self.assertIn('<playcount>5</playcount>',dest.with_suffix('.nfo').read_text())
    def test_reset_backup_restore_preserves_video_subtitle_and_unknown_nfo(self):
        (self.show/'poster.jpg').write_bytes(b'POSTER');(self.show/'unrelated.nfo').write_bytes(b'KEEP');headers=self.unlock()
        preview=self.client.post('/api/tv/reset/preview',json={'folder':self.show.name});self.assertEqual(preview.status_code,200,preview.text)
        result=self.client.post('/api/tv/reset',json={'folder':self.show.name,'review_token':preview.json()['review_token']},headers=headers);self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(self.video.exists());self.assertTrue((self.season/'Show.S01E01.en.srt').exists());self.assertEqual((self.show/'unrelated.nfo').read_bytes(),b'KEEP')
        backups=self.client.get('/api/tv/backups',params={'folder':self.show.name}).json()['backups'];b=next(b for b in backups if b['file']=='tvshow.nfo');payload={'folder':self.show.name,'backup_id':b['id']};p=self.client.post('/api/tv/backups/preview',json=payload)
        result=self.client.post('/api/tv/backups/restore',json={**payload,'review_token':p.json()['review_token']},headers=headers);self.assertEqual(result.status_code,200,result.text);self.assertIn('Local plot',(self.show/'tvshow.nfo').read_text())
    def test_unsafe_paths_and_mismatched_library_rejected(self):
        self.assertEqual(self.client.post('/api/tv/inspect',json={'folder':'../Movies'}).status_code,400)
        external=self.base/'Outside';external.mkdir();(self.show/'actors').symlink_to(external,target_is_directory=True)
        payload=self.payload(artwork={'../bad.jpg':'https://assets.fanart.tv/fanart/test.jpg'})
        self.assertEqual(self.client.post('/api/tv/write/preview',json=payload).status_code,400)
        cfg=main.load_config();cfg['tv_root_path']=cfg['root_path'];main.save_config(cfg);self.assertEqual(self.client.post('/api/tv/inspect',json={'folder':self.show.name}).status_code,400)
    def test_folder_rename_keeps_backup_association(self):
        response=self.client.post('/api/tv/write',json=self.approve(self.payload(folder_name='Renamed Show')),headers=self.unlock());self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((self.root/'Renamed Show'/'Season 01'/self.video.name).read_bytes(),b'VIDEO MUST NEVER CHANGE');self.assertTrue(self.client.get('/api/tv/backups',params={'folder':'Renamed Show'}).json()['backups'])
    def test_partial_provider_failure_does_not_replace_complete_inventory(self):
        with patch.object(main.tv_services['provider'],'bundle',new=AsyncMock(side_effect=main.HTTPException(502,'Page failed'))):
            r=self.client.post('/api/tv/match',json={'folder':self.show.name,'tvdb_id':123})
        self.assertEqual(r.status_code,502)
        with main.database() as db:self.assertEqual(json.loads(db.execute('SELECT data FROM provider_cache WHERE key=?',('tv-series:123:official:eng',)).fetchone()[0]),self.bundle)

    def test_tv_searches_persist_archive_restore_and_stay_separate(self):
        payload={'name':'Missing episodes','filters':{'missing':'yes'}}
        self.assertEqual(self.client.post('/api/tv/saved-searches',json=payload).status_code,423)
        headers=self.unlock();r=self.client.post('/api/tv/saved-searches',json=payload,headers=headers);self.assertEqual(r.status_code,200,r.text);identifier=r.json()['id']
        self.assertEqual(len(self.client.get('/api/tv/saved-searches').json()['searches']),1)
        self.assertEqual(self.client.get('/api/saved-searches').json()['searches'],[])
        self.client.delete('/api/tv/saved-searches/'+identifier,headers=headers)
        self.assertTrue(self.client.get('/api/tv/saved-searches').json()['searches'][0]['archived'])
        self.assertEqual(self.client.post('/api/tv/saved-searches',json={**payload,'id':identifier},headers=headers).status_code,200)
        self.assertFalse(self.client.get('/api/tv/saved-searches').json()['searches'][0]['archived'])
    def test_manual_episode_edits_are_reviewed_and_preserve_video(self):
        payload=self.payload(episode_metadata={'Season 01/Show.S01E01.mkv':{'title':'My episode title','certificate':'NR'}})
        plan=self.client.post('/api/tv/write/preview',json=payload);self.assertEqual(plan.status_code,200,plan.text)
        self.assertTrue(any('My episode title'==field['after'] for field in plan.json()['metadata']))
        result=self.client.post('/api/tv/write',json={**payload,'review_token':plan.json()['review_token']},headers=self.unlock());self.assertEqual(result.status_code,200,result.text)
        self.assertIn('My episode title',self.video.with_suffix('.nfo').read_text());self.assertIn('<mpaa>NR</mpaa>',self.video.with_suffix('.nfo').read_text());self.assertEqual(self.video.read_bytes(),b'VIDEO MUST NEVER CHANGE')
    def test_sample_subtitle_is_not_renamed_with_main_episode(self):
        (self.season/'Show.S01E01.sample.mkv').write_bytes(b'SAMPLE')
        extra_sub=self.season/'Show.S01E01.sample.en.srt';extra_sub.write_bytes(b'EXTRA SUBTITLE')
        result=self.client.post('/api/tv/write',json=self.approve(self.payload(rename_episodes=True)),headers=self.unlock());self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(extra_sub.read_bytes(),b'EXTRA SUBTITLE')
    def test_rename_conflict_preserves_all_originals(self):
        conflict=self.season/'Example Show - S01E01 - Pilot.mkv';conflict.write_bytes(b'CONFLICT')
        r=self.client.post('/api/tv/write/preview',json=self.payload(rename_episodes=True));self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.video.read_bytes(),b'VIDEO MUST NEVER CHANGE');self.assertEqual(conflict.read_bytes(),b'CONFLICT')
    def test_relocking_during_image_download_prevents_commit(self):
        headers=self.unlock();token=headers['X-Artflix-Edit-Token'];payload=self.payload(artwork_only=True,metadata={},artwork={'fanart.jpg':'https://assets.fanart.tv/test.jpg'})
        approved=self.approve(payload)
        async def downloading(*args):main.edit_sessions.lock(token);return b'IMAGE'
        with patch.object(main,'download_image',side_effect=downloading):r=self.client.post('/api/tv/write',json=approved,headers=headers)
        self.assertEqual(r.status_code,423,r.text);self.assertFalse((self.show/'fanart.jpg').exists())
    def test_artwork_only_does_not_rewrite_nfo_and_saved_source_persists(self):
        before=(self.show/'tvshow.nfo').read_bytes();url='https://assets.fanart.tv/test.jpg'
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'IMAGE')):
            payload=self.payload(artwork_only=True,metadata={},artwork={'fanart.jpg':url});r=self.client.post('/api/tv/write',json=self.approve(payload),headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual((self.show/'tvshow.nfo').read_bytes(),before)
        data=self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json();self.assertEqual(data['saved_assets']['artwork']['fanart.jpg'],url)
    def test_tv_preflight_does_not_create_files_in_share(self):
        before=sorted(str(p) for p in self.root.rglob('*'));result=self.client.get('/api/deployment/checks').json();self.assertTrue(any(c['key']=='tv_mapping' for c in result['checks']));self.assertEqual(before,sorted(str(p) for p in self.root.rglob('*')))

    def test_downloaded_actor_portrait_remains_after_later_metadata_save(self):
        self.bundle['details']['characters']=[{'peopleId':7,'personName':'Actor One','name':'A Character','personImgURL':'https://artworks.thetvdb.com/person.jpg'}]
        main.cache_provider('tv-series:123:official:eng',self.bundle)
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'PORTRAIT')):
            r=self.client.post('/api/tv/write',json=self.approve(self.payload(actor_ids=['7'])),headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.post('/api/tv/write',json=self.approve(self.payload(metadata={'title':'Later edit'})),headers=self.unlock());self.assertEqual(r.status_code,200,r.text)
        data=self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json()
        self.assertEqual(data['saved_assets']['actor_ids'],['7']);self.assertIn('/api/tv/image?',data['metadata']['actors'][0]['local_image']);self.assertEqual((self.show/'actors'/'Actor One--7.jpg').read_bytes(),b'PORTRAIT')

    def test_rename_example_matches_review_and_never_changes_files(self):
        before={p.relative_to(self.show).as_posix():p.read_bytes() for p in self.show.rglob('*') if p.is_file()}
        pattern='{show} S{season}E{episode} - {title}'
        response=self.client.post('/api/tv/rename-example',json={'folder':self.show.name,'video':'Season 01/Show.S01E01.mkv','tvdb_id':123,'title':'My: Show','filename_format':pattern})
        self.assertEqual(response.status_code,200,response.text);example=response.json()
        self.assertEqual(example['after'],'Season 01/My Show S01E01 - Pilot.mkv')
        review=self.client.post('/api/tv/write/preview',json=self.payload(rename_episodes=True,metadata={'title':'My: Show'},filename_format=pattern))
        self.assertEqual(review.status_code,200,review.text)
        self.assertIn({'before':example['before'],'after':example['after']},review.json()['renames'])
        self.assertEqual(before,{p.relative_to(self.show).as_posix():p.read_bytes() for p in self.show.rglob('*') if p.is_file()})
    def test_rename_example_combined_episodes_errors_and_conflicts(self):
        combined=self.season/'Show.S01E01E02.mkv';combined.write_bytes(b'COMBINED')
        payload={'folder':self.show.name,'video':'Season 01/'+combined.name,'tvdb_id':123,'title':'Example Show'}
        result=self.client.post('/api/tv/rename-example',json=payload);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['after'],'Season 01/Example Show - S01E01E02 - Pilot + Second.mkv')
        (self.show/result.json()['after']).write_bytes(b'EXISTING')
        self.assertTrue(self.client.post('/api/tv/rename-example',json=payload).json()['conflict'])
        self.assertEqual(self.client.post('/api/tv/rename-example',json={**payload,'video':'../outside.mkv'}).status_code,400)
        self.assertEqual(self.client.post('/api/tv/rename-example',json={**payload,'filename_format':'{unknown}'}).status_code,400)
        self.assertEqual(self.client.post('/api/tv/rename-example',json={**payload,'tvdb_id':999}).status_code,409)

    def test_refresh_imports_only_owned_episode_credits_and_caches_them(self):
        self.bundle['details']['characters']=[{'peopleType':'Director','personName':'Series Director'},{'peopleType':'Writer','personName':'Series Writer'},{'peopleType':'Actor','personName':'Actor One','name':'Character'}]
        extended={'id':1,'seriesId':123,'characters':[{'type':42,'personName':'Episode Director'},{'peopleType':'Writer','personName':'Episode Writer'},{'peopleType':'Writer','personName':'Episode Writer'}]}
        async def get(path,params=None):
            if path=='/episodes/1/extended':return {'data':extended}
            if path=='/people/types':return {'data':[{'id':42,'name':'Director'}]}
            raise AssertionError('Unexpected provider request '+path)
        with patch.object(main.tv_services['provider'],'bundle',new=AsyncMock(return_value=self.bundle)),patch.object(main.tv_services['provider'],'get',side_effect=get) as calls:
            result=self.client.post('/api/tv/match',json={'folder':self.show.name,'tvdb_id':123})
            self.assertEqual(result.status_code,200,result.text);data=result.json()['bundle']
            self.assertEqual(data['details']['credits']['writers'],'Series Writer')
            self.assertEqual(data['episodes'][0]['credits'],{'writers':'Episode Writer','directors':'Episode Director'})
            self.assertEqual(calls.call_count,2)
            result=self.client.post('/api/tv/match',json={'folder':self.show.name,'tvdb_id':123});self.assertEqual(result.status_code,200,result.text);self.assertEqual(calls.call_count,2)
        inspected=self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json()
        self.assertEqual(inspected['inventory']['episodes'][0]['provider_credits']['writers'],'Episode Writer')
        result=self.client.post('/api/tv/write',json=self.approve(self.payload(episode_nfos=True)),headers=self.unlock());self.assertEqual(result.status_code,200,result.text)
        episode=self.video.with_suffix('.nfo').read_text();show=(self.show/'tvshow.nfo').read_text()
        self.assertIn('<director>Episode Director</director>',episode);self.assertIn('<credits>Episode Writer</credits>',episode)
        self.assertIn('<director>Series Director</director>',show);self.assertNotIn('<name>Series Director</name>',show);self.assertIn('<name>Actor One</name>',show)
    def test_import_preserves_existing_and_manually_overridden_episode_credits(self):
        self.bundle['episodes'][0]['credits']={'writers':'Provider Writer','directors':'Provider Director'}
        main.cache_provider('tv-series:123:official:eng',self.bundle)
        self.video.with_suffix('.nfo').write_text('<episodedetails><credits>My Writer</credits><director>My Director</director><playcount>7</playcount></episodedetails>')
        payload=self.payload(episode_nfos=True,episode_metadata={'Season 01/Show.S01E01.mkv':{'writers':'My revised writer'}})
        r=self.client.post('/api/tv/write',json=self.approve(payload),headers=self.unlock());self.assertEqual(r.status_code,200,r.text)
        content=self.video.with_suffix('.nfo').read_text();self.assertIn('<director>My Director</director>',content);self.assertIn('<credits>My revised writer</credits>',content);self.assertIn('<playcount>7</playcount>',content)
    def test_unavailable_episode_credit_keeps_completeness_and_local_credits(self):
        self.video.with_suffix('.nfo').write_text('<episodedetails><credits>Local Writer</credits></episodedetails>')
        with patch.object(main.tv_services['provider'],'bundle',new=AsyncMock(return_value=self.bundle)),patch.object(main.tv_services['provider'],'get',new=AsyncMock(side_effect=main.HTTPException(502,'Unavailable'))):
            r=self.client.post('/api/tv/match',json={'folder':self.show.name,'tvdb_id':123})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['completeness']['counts']['missing'],1);self.assertEqual(r.json()['bundle']['credit_status']['unavailable'],1)
        r=self.client.post('/api/tv/write',json=self.approve(self.payload(episode_nfos=True)),headers=self.unlock());self.assertEqual(r.status_code,200,r.text);self.assertIn('<credits>Local Writer</credits>',self.video.with_suffix('.nfo').read_text())

    def test_tv_search_separates_folder_year_from_numeric_title(self):
        async def get(path,params=None):return {'data':[{'name':'11 Minutes','year':'2022','tvdb_id':'425289'}]}
        with patch.object(main.tv_services['provider'],'get',side_effect=get) as call:
            r=self.client.post('/api/tv/search',json={'query':'11 Minutes (2022)'})
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(call.call_args.args[1],{'query':'11 Minutes','year':2022,'type':'series','limit':15})
            self.client.post('/api/tv/search',json={'query':'1899'})
            self.assertEqual(call.call_args.args[1]['query'],'1899');self.assertNotIn('year',call.call_args.args[1])
            self.client.post('/api/tv/search',json={'query':'11 Minutes (2022)','year':2023});self.assertEqual(call.call_args.args[1]['year'],2023)
    def test_single_episode_thumbnail_is_reviewed_without_rewriting_nfo(self):
        url='https://artworks.thetvdb.com/banners/episodes/123/1.jpg';self.bundle['episodes'][0]['image']=url;main.cache_provider('tv-series:123:official:eng',self.bundle)
        nfo=(self.show/'tvshow.nfo').read_bytes();payload=self.payload(metadata={},artwork_only=True,thumbnail_files=['Season 01/Show.S01E01.mkv'])
        approved=self.approve(payload)
        self.assertEqual(self.client.post('/api/tv/write',json=approved).status_code,423)
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'THUMBNAIL')):
            r=self.client.post('/api/tv/write',json=approved,headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text);image=self.season/'Show.S01E01-thumb.jpg';self.assertEqual(image.read_bytes(),b'THUMBNAIL');self.assertEqual((self.show/'tvshow.nfo').read_bytes(),nfo);self.assertEqual(self.video.read_bytes(),b'VIDEO MUST NEVER CHANGE')
        data=self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json();episode=data['inventory']['episodes'][0]
        self.assertIn('/api/tv/image?',episode['thumbnail']['url']);self.assertEqual(episode['provider_thumbnail'],url);self.assertEqual(data['saved_assets']['artwork']['Season 01/Show.S01E01-thumb.jpg'],url)
    def test_bulk_thumbnails_preserve_existing_and_generate_nfo_reference(self):
        self.bundle['episodes'][0]['image']='https://artworks.thetvdb.com/episode.jpg';main.cache_provider('tv-series:123:official:eng',self.bundle)
        image=self.season/'Show.S01E01-thumb.jpg';image.write_bytes(b'MY EXISTING IMAGE')
        payload=self.payload(metadata={},artwork_only=True,episode_thumbnails=True,replace_existing=False)
        with patch.object(main,'download_image',new=AsyncMock(side_effect=AssertionError('Must keep existing thumb'))):
            r=self.client.post('/api/tv/write',json=self.approve(payload),headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(image.read_bytes(),b'MY EXISTING IMAGE')
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'NEW THUMB')):
            r=self.client.post('/api/tv/write',json=self.approve(self.payload(episode_thumbnails=True,episode_nfos=True,rename_episodes=True)),headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text);renamed=self.season/'Example Show - S01E01 - Pilot-thumb.jpg';self.assertEqual(renamed.read_bytes(),b'NEW THUMB');self.assertIn('<thumb>Example Show - S01E01 - Pilot-thumb.jpg</thumb>',renamed.with_name('Example Show - S01E01 - Pilot.nfo').read_text())
        backups=self.client.get('/api/tv/backups',params={'folder':self.show.name}).json()['backups'];self.assertTrue(any(b['file']=='Season 01/Show.S01E01-thumb.jpg' for b in backups))
    def test_thumbnail_move_updates_reference_and_reset_preserves_video_subtitles(self):
        (self.season/'Show.S01E01-thumb.png').write_bytes(b'MY PNG')
        self.video.with_suffix('.nfo').write_text('<episodedetails><thumb>Show.S01E01-thumb.png</thumb><playcount>4</playcount></episodedetails>')
        r=self.client.post('/api/tv/write',json=self.approve(self.payload(rename_episodes=True)),headers=self.unlock());self.assertEqual(r.status_code,200,r.text)
        new=self.season/'Example Show - S01E01 - Pilot-thumb.png';self.assertEqual(new.read_bytes(),b'MY PNG')
        content=new.with_name('Example Show - S01E01 - Pilot.nfo').read_text();self.assertIn('Example Show - S01E01 - Pilot-thumb.png',content);self.assertIn('<playcount>4</playcount>',content)
        preview=self.client.post('/api/tv/reset/preview',json={'folder':self.show.name}).json();self.assertTrue(any(f['path'].endswith('-thumb.png') for f in preview['files']))
        r=self.client.post('/api/tv/reset',json={'folder':self.show.name,'review_token':preview['review_token']},headers=self.unlock());self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((self.season/'Example Show - S01E01 - Pilot.mkv').read_bytes(),b'VIDEO MUST NEVER CHANGE');self.assertEqual((self.season/'Example Show - S01E01 - Pilot.en.srt').read_bytes(),b'SUBTITLE')
    def test_thumbnail_download_rejects_unknown_file_and_untrusted_image_url(self):
        payload=self.payload(metadata={},artwork_only=True,thumbnail_files=['../outside.mkv'])
        self.assertEqual(self.client.post('/api/tv/write/preview',json=payload).status_code,400)
        self.bundle['episodes'][0]['image']='http://127.0.0.1/private.jpg';main.cache_provider('tv-series:123:official:eng',self.bundle)
        r=self.client.post('/api/tv/write/preview',json={**payload,'thumbnail_files':['Season 01/Show.S01E01.mkv']});self.assertEqual(r.status_code,409)
        self.assertFalse((self.season/'Show.S01E01-thumb.jpg').exists())

    def test_cached_relative_thumbnail_paths_download_without_provider_refresh(self):
        path='/banners/v4/episode/9366122/screencap/646644673b10e.jpg'
        self.bundle['episodes'][0]['image']=path;main.cache_provider('tv-series:123:official:eng',self.bundle)
        self.assertIn('Artflix version '+main.VERSION,self.client.get('/').text)
        with patch.object(main.tv_services['provider'],'get',new=AsyncMock(side_effect=AssertionError('No provider refresh needed'))):
            inspected=self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json()
            self.assertEqual(inspected['inventory']['episodes'][0]['provider_thumbnail'],'https://artworks.thetvdb.com'+path)
            payload=self.payload(episode_thumbnails=True)
            preview=self.client.post('/api/tv/write/preview',json=payload);self.assertEqual(preview.status_code,200,preview.text)
            self.assertEqual(preview.json()['thumbnails']['ready'],1)
            with patch.object(main,'download_image',new=AsyncMock(return_value=b'IMAGE')) as download:
                r=self.client.post('/api/tv/write',json={**payload,'review_token':preview.json()['review_token']},headers=self.unlock())
                self.assertEqual(download.call_args.args[1],'https://artworks.thetvdb.com'+path)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['thumbnails']['downloaded'],1);self.assertEqual((self.season/'Show.S01E01-thumb.jpg').read_bytes(),b'IMAGE')
    def test_bulk_thumbnail_results_explain_existing_unavailable_and_unmatched_files(self):
        self.bundle['episodes'][0]['image']='/banners/first.jpg'
        self.bundle['episodes'][1]['image']='banners/second.jpg'
        for n in (2,3,8):(self.season/f'Show.S01E{n:02d}.mkv').write_bytes(b'VIDEO')
        (self.season/'Show.S01E01-thumb.jpg').write_bytes(b'EXISTING')
        main.cache_provider('tv-series:123:official:eng',self.bundle)
        payload=self.payload(metadata={},artwork_only=True,episode_thumbnails=True,replace_existing=False)
        preview=self.client.post('/api/tv/write/preview',json=payload);self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['thumbnails'],{'requested':4,'ready':1,'skipped_existing':1,'unavailable':1,'unmatched':1})
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'SECOND')):
            r=self.client.post('/api/tv/write',json={**payload,'review_token':preview.json()['review_token']},headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['thumbnails']['downloaded'],1);self.assertEqual((self.season/'Show.S01E01-thumb.jpg').read_bytes(),b'EXISTING');self.assertEqual((self.season/'Show.S01E02-thumb.jpg').read_bytes(),b'SECOND')

    def test_review_subset_keeps_unchecked_nfo_and_downloads_only_chosen_image(self):
        before=(self.show/'tvshow.nfo').read_bytes();payload=self.payload(artwork={'fanart.jpg':'https://assets.fanart.tv/a.jpg','poster.jpg':'https://assets.fanart.tv/b.jpg'})
        approved=self.approve(payload)
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'CHOSEN')) as download:
            r=self.client.post('/api/tv/write',json={**approved,'selected_operations':['file:poster.jpg']},headers=self.unlock())
            self.assertEqual(download.call_count,1)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual((self.show/'tvshow.nfo').read_bytes(),before);self.assertFalse((self.show/'fanart.jpg').exists());self.assertEqual((self.show/'poster.jpg').read_bytes(),b'CHOSEN')
    def test_subset_cannot_add_operations_or_bypass_review_invalidation(self):
        approved=self.approve(self.payload())
        for selected in ([],['file:../outside.jpg'],['file:tvshow.nfo','file:tvshow.nfo']):
            r=self.client.post('/api/tv/write',json={**approved,'selected_operations':selected},headers=self.unlock());self.assertEqual(r.status_code,400,r.text)
        r=self.client.post('/api/tv/write',json={**approved,'metadata':{'title':'Unreviewed'},'selected_operations':['file:tvshow.nfo']},headers=self.unlock());self.assertEqual(r.status_code,409)
    def test_subset_can_exclude_video_folder_rename_and_remaps_generated_sidecars(self):
        payload=self.payload(rename_episodes=True,episode_nfos=True,folder_name='Other Folder')
        preview=self.client.post('/api/tv/write/preview',json=payload);self.assertEqual(preview.status_code,200,preview.text)
        episode=next(f for f in preview.json()['files'] if f['path'].endswith('Pilot.nfo'));self.assertEqual(episode['without_rename'],'Season 01/Show.S01E01.nfo')
        r=self.client.post('/api/tv/write',json={**payload,'review_token':preview.json()['review_token'],'selected_operations':['file:'+episode['path']]},headers=self.unlock());self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(self.video.exists());self.assertFalse((self.root/'Other Folder').exists());self.assertTrue(self.video.with_suffix('.nfo').is_file());self.assertEqual((self.show/'tvshow.nfo').read_text().find('Local plot')>=0,True)
    def test_unselected_thumbnail_retains_valid_remote_nfo_reference(self):
        url='https://artworks.thetvdb.com/image.jpg';self.bundle['episodes'][0]['image']=url;main.cache_provider('tv-series:123:official:eng',self.bundle)
        payload=self.payload(episode_nfos=True,episode_thumbnails=True);approved=self.approve(payload)
        r=self.client.post('/api/tv/write',json={**approved,'selected_operations':['file:Season 01/Show.S01E01.nfo']},headers=self.unlock());self.assertEqual(r.status_code,200,r.text)
        self.assertIn('<thumb>'+url+'</thumb>',self.video.with_suffix('.nfo').read_text());self.assertFalse((self.season/'Show.S01E01-thumb.jpg').exists())
    def test_private_config_backup_uses_sqlite_snapshot_and_excludes_previous_archives(self):
        import io,zipfile,sqlite3
        self.assertEqual(self.client.post('/api/config/backups').status_code,423)
        headers=self.unlock();r=self.client.post('/api/config/backups',headers=headers);self.assertEqual(r.status_code,200,r.text);identifier=r.json()['id']
        self.assertEqual(self.client.get('/api/config/backups/'+identifier).status_code,423)
        download=self.client.get('/api/config/backups/'+identifier,headers=headers);self.assertEqual(download.status_code,200)
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertIn('settings.json',archive.namelist());self.assertIn('artflix.sqlite3',archive.namelist());self.assertIn('TV-SECRET',archive.read('settings.json').decode());self.assertFalse(any('config-backups/' in n for n in archive.namelist()))
            restored=self.base/'snapshot.sqlite3';restored.write_bytes(archive.read('artflix.sqlite3'))
            with sqlite3.connect(restored) as db:self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
        second=self.client.post('/api/config/backups',headers=headers);self.assertEqual(second.status_code,200);self.assertEqual(len(self.client.get('/api/config/backups',headers=headers).json()['backups']),2)
        self.assertEqual((self.config/'config-backups'/(identifier+'.zip')).stat().st_mode&0o777,0o600)
    def test_saved_tv_format_persists_through_settings_save_and_invalid_format_rejected(self):
        payload={'name':'My system','pattern':'{show} S{season}E{episode}','make_default':True}
        self.assertEqual(self.client.post('/api/tv/naming-templates',json=payload).status_code,423)
        headers=self.unlock();r=self.client.post('/api/tv/naming-templates',json=payload,headers=headers);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json()['filename_format'],payload['pattern'])
        cfg=self.client.get('/api/settings').json();cfg.pop('tv_filename_format');cfg.pop('tv_naming_templates');self.assertEqual(self.client.put('/api/settings',json=cfg,headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/tv/naming-templates').json()['default'],payload['pattern'])
        self.assertEqual(self.client.post('/api/tv/naming-templates',json={**payload,'pattern':'../{title}'},headers=headers).status_code,400)
    def test_season_artwork_can_save_selected_output_and_excludes_arbitrary_paths(self):
        payload=self.payload(metadata={},artwork_only=True,artwork={'season01-poster.jpg':'https://assets.fanart.tv/season.jpg','Season 01/poster.jpg':'https://assets.fanart.tv/season.jpg'})
        with patch.object(main,'download_image',new=AsyncMock(return_value=b'SEASON POSTER')):
            r=self.client.post('/api/tv/write',json={**self.approve(payload),'selected_operations':['file:Season 01/poster.jpg']},headers=self.unlock())
        self.assertEqual(r.status_code,200,r.text);self.assertFalse((self.show/'season01-poster.jpg').exists());self.assertEqual((self.season/'poster.jpg').read_bytes(),b'SEASON POSTER')
        inspected=self.client.post('/api/tv/inspect',json={'folder':self.show.name}).json();self.assertTrue(any(a['name']=='Season 01/poster.jpg' for a in inspected['artwork']))
        self.assertEqual(self.client.post('/api/tv/write/preview',json={**payload,'artwork':{'Season 999/poster.jpg':'https://assets.fanart.tv/s.jpg'}}).status_code,400)
    def test_nfo_sample_validation_is_read_only_and_finds_missing_local_image(self):
        self.video.with_suffix('.nfo').write_text('<episodedetails><title>Pilot</title><season>1</season><episode>1</episode><thumb>missing.jpg</thumb></episodedetails>')
        before={str(p):p.read_bytes() for p in self.show.rglob('*') if p.is_file()};r=self.client.get('/api/nfo/sample-check');self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(any(not row['valid'] and any('artwork' in issue for issue in row['issues']) for row in r.json()['results']));self.assertEqual(before,{str(p):p.read_bytes() for p in self.show.rglob('*') if p.is_file()})


class EpisodeInventoryTests(unittest.TestCase):
    def test_relative_tvdb_image_resolution_preserves_safe_host_boundary(self):
        self.assertEqual(tvdb_image_url('/banners/v4/episode/test.jpg'),'https://artworks.thetvdb.com/banners/v4/episode/test.jpg')
        self.assertEqual(tvdb_image_url('banners/test.png'),'https://artworks.thetvdb.com/banners/test.png')
        for path in ('//evil.example/image.jpg','https://evil.example/banners/x.jpg','http://127.0.0.1/image.jpg','/private/image.jpg',None):self.assertEqual(tvdb_image_url(path),'')
    def test_crew_roles_are_not_character_names_or_actor_portraits(self):
        record={'characters':[{'peopleType':'Director','personName':'Jane Director','name':'Director'},{'peopleType':'Writer','personName':'Sam Writer'},{'peopleType':'Writer','personName':'sam writer'},{'peopleType':'Creator','personName':'Creator Name'},{'peopleType':'Actor','personName':'Cast Name','name':'Character Name'},{'peopleType':'Director','name':'Not a person name'}]}
        self.assertEqual(normalize_credits(record),{'directors':'Jane Director','writers':'Sam Writer'})
        self.assertEqual([c['personName'] for c in tv_cast(record)],['Cast Name'])
    def test_combined_episode_and_range_parsing(self):
        self.assertEqual(episode_numbers('Show.S01E01E02'),[(1,1),(1,2)])
        self.assertEqual(episode_numbers('Show.S01E01-E03'),[(1,1),(1,2),(1,3)])
        self.assertEqual(episode_numbers('Show.2x03'),[(2,3)])
        self.assertEqual(episode_numbers('Show.1080p'),[])
    def test_completeness_deduplicates_versions_and_excludes_upcoming_specials(self):
        local={'episodes':[{'numbers':[[1,1],[1,2]]},{'numbers':[[1,1]]}]}
        remote=[{'seasonNumber':1,'number':n,'aired':air} for n,air in [(1,'2024-01-01'),(2,'2024-01-02'),(3,'9999-01-01'),(4,None)]]+[{'seasonNumber':0,'number':1,'aired':'2024-01-01'}]
        counts=completeness(local,remote)['counts'];self.assertEqual(counts['percent'],100);self.assertEqual(counts['missing'],0);self.assertEqual(counts['specials'],1);self.assertEqual(counts['unknown'],1)


class TVDBAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_login_localization_and_all_episode_pages(self):
        import httpx
        requests=[]
        def handle(request):
            requests.append(request)
            if request.url.path.endswith('/login'):
                self.assertEqual(json.loads(request.content),{'apikey':'PRIVATE KEY','pin':'PRIVATE PIN'})
                return httpx.Response(200,json={'data':{'token':'PRIVATE BEARER'}})
            self.assertEqual(request.headers.get('Authorization'),'Bearer PRIVATE BEARER')
            if request.url.path.endswith('/extended'):return httpx.Response(200,json={'data':{'id':7,'name':'Fallback','translations':{'nameTranslations':[{'language':'fra','name':'Titre'}],'overviewTranslations':[{'language':'fra','overview':'Résumé'}]}}})
            page=int(request.url.params.get('page','0'))
            return httpx.Response(200,json={'data':{'episodes':[{'id':page+1,'seasonNumber':1,'number':page+1}]},'links':{'next':'https://api4.thetvdb.com/v4/ignored' if page==0 else None}})
        factory=httpx.AsyncClient
        with patch('app.television.httpx.AsyncClient',side_effect=lambda **kw:factory(transport=httpx.MockTransport(handle),**kw)):
            data=await TVDB(lambda:{'tvdb_key':'PRIVATE KEY','tvdb_pin':'PRIVATE PIN'}).bundle(7,'official','fra')
        self.assertEqual(data['details']['name'],'Titre');self.assertEqual(data['details']['overview'],'Résumé');self.assertEqual(len(data['episodes']),2)
        self.assertEqual(len([r for r in requests if r.url.path.endswith('/login')]),1)
    async def test_repeated_provider_page_fails_instead_of_claiming_completeness(self):
        import httpx
        def handle(request):
            if request.url.path.endswith('/login'):return httpx.Response(200,json={'data':{'token':'token'}})
            if request.url.path.endswith('/extended'):return httpx.Response(200,json={'data':{'id':7}})
            return httpx.Response(200,json={'data':{'episodes':[{'id':1}]},'links':{'next':'next'}})
        factory=httpx.AsyncClient
        with patch('app.television.httpx.AsyncClient',side_effect=lambda **kw:factory(transport=httpx.MockTransport(handle),**kw)):
            with self.assertRaises(main.HTTPException):await TVDB(lambda:{'tvdb_key':'key'}).bundle(7,'official','eng')

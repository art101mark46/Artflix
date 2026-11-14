"""TV endpoints with separate roots/index, explicit matching and reviewed mutations."""
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.control import COMMIT_LOCK, fingerprint
from app.filesystem import rename_media_files, rename_no_replace
from app.operations import file_signature
from app.tv_artwork import tvdb_choices, tmdb_choices
from app.television import TVDB, completeness, inventory, read_nfo, values, serialize_nfo, safe_image, tv_language, normalize_credits, tv_cast, tvdb_image_url, normalize_episode_images, season_artwork_layout
from app.review_selection import select_operations, subset_outputs
from app.jobs import ScanJobs


class TVFolder(BaseModel):
    folder: str

class TVNamingTemplate(BaseModel):
    name: str=Field(min_length=1,max_length=80)
    pattern: str=Field(min_length=1,max_length=180)
    make_default: bool=True


class TVSearch(BaseModel):
    query: str=Field(min_length=1,max_length=200)
    year: int|None=Field(default=None,ge=1800,le=9999)

class TVMatch(TVFolder):
    tvdb_id: int=Field(gt=0)
    order: str='official'

class TVWrite(TVFolder):
    tvdb_id: int=Field(ge=0)
    metadata: dict[str,str]=Field(default_factory=dict)
    artwork: dict[str,str]=Field(default_factory=dict)
    actor_ids: list[str]=Field(default_factory=list)
    artwork_only: bool=False
    episode_metadata: dict[str,dict[str,str]]=Field(default_factory=dict)
    episode_nfos: bool=False
    episode_thumbnails: bool=False
    thumbnail_files: list[str]=Field(default_factory=list)
    replace_existing: bool=False
    rename_episodes: bool=False
    multi_episode_filename_format: str|None=None
    filename_format: str='{show} - S{season}E{episode} - {title}'
    folder_name: str|None=None
    order: str='official'
    review_token: str|None=None
    selected_operations: list[str]|None=None

class TVDisplayImage(TVFolder):
    name: str=""

class TVRenameExample(TVFolder):
    video: str
    tvdb_id: int=Field(ge=0)
    order: str="official"
    title: str=""
    multi_episode_filename_format: str|None=None
    multi_episode_filename_format: str|None=None
    filename_format: str="{show} - S{season}E{episode} - {title}"


class TVReset(TVFolder):
    review_token: str|None=None

class TVSavedSearch(BaseModel):
    id: str|None=None
    name: str=Field(min_length=1,max_length=80)
    filters: dict[str,str]=Field(default_factory=dict)


class TVRestore(TVFolder):
    backup_id: str
    review_token: str|None=None


def install_tv(app,m):
    provider=TVDB(m.load_config);jobs=ScanJobs()
    def db():
        ctx=m.database()
        return ctx
    def init():
        with db() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS tv_display_images(path TEXT PRIMARY KEY,name TEXT)')
            connection.execute('CREATE TABLE IF NOT EXISTS tv_library(path TEXT PRIMARY KEY,data TEXT)')
            connection.execute('CREATE TABLE IF NOT EXISTS tv_saved_searches(id TEXT PRIMARY KEY,name TEXT,filters TEXT,archived INTEGER NOT NULL DEFAULT 0)')
            connection.execute('CREATE TABLE IF NOT EXISTS tv_backups(id TEXT PRIMARY KEY,folder TEXT,file TEXT,created REAL)')
    def roots():
        cfg=m.load_config()
        if not cfg.get('tv_root_path') or not cfg.get('tv_write_path'):raise HTTPException(400,'Set the TV scan and write paths in Settings first.')
        root,write=(Path(cfg[k]).resolve() for k in ('tv_root_path','tv_write_path'))
        if any(not p.is_dir() for p in (root,write)):raise HTTPException(400,'Configured TV paths must be mounted directories.')
        movie_roots=[Path(cfg[k]).resolve() for k in ('root_path','write_path')]
        if any(a.exists() and b.exists() and a.samefile(b) for a in (root,write) for b in movie_roots):raise HTTPException(400,'TV and movie mappings refer to the same share.')
        if any(a==b or a in b.parents or b in a.parents for a in (root,write) for b in movie_roots):raise HTTPException(400,'TV paths must be separate from movie paths.')
        return root,write
    def folder_pair(name):
        root,write=roots()
        if Path(name).name!=name or name in {'.','..'}:raise HTTPException(400,'Invalid TV show folder.')
        source,target=root/name,write/name
        if any(p.is_symlink() or not p.is_dir() for p in (source,target)):raise HTTPException(400,'TV folder is missing or is a symlink.')
        if not source.samefile(target):raise HTTPException(400,'TV scan/write mappings must refer to the same show folder.')
        return source,target
    def cached(key):
        with db() as connection:row=connection.execute('SELECT data FROM provider_cache WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None
    def bundle_key(series_id,order):return f'tv-series:{series_id}:{order}:{tv_language(m.load_config())}'
    def bundle(series_id,order):
        data=cached(bundle_key(series_id,order))
        return normalize_episode_images(data) if data else None
    def order_valid(order):
        if order!='official':raise HTTPException(400,'This release supports aired episode order. Choose Aired order.')
    def tree(folder):
        result={}
        for directory,dirs,files in os.walk(folder,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
            for name in files:
                file=Path(directory)/name;result[file.relative_to(folder).as_posix()]=file_signature(file)
        return result
    def show_record(folder,probe=False,cancelled=None):
        data=values(read_nfo(folder/'tvshow.nfo','tvshow'))
        inv=inventory(folder,m.VIDEO_EXTENSIONS,m.video_info if probe else None,cancelled)
        try:series_id=int(data.get('tvdb_id') or 0)
        except ValueError:series_id=0
        state={}
        statefile=folder/'.artflix-tv.json'
        if statefile.is_file() and not statefile.is_symlink():
            try:state=json.loads(statefile.read_text())
            except (ValueError,OSError):pass
        order=state.get('order','official');remote=bundle(series_id,order) if series_id else None
        missing=[key for key in ('title','plot','year','certificate','genres','tvdb_id') if not data.get(key)]
        for key,prefix in [('poster','poster'),('fanart','fanart')]:
            if not any((folder/(prefix+suffix)).is_file() for suffix in ('.jpg','.png','.webp')):missing.append(key)
        return {'folder':folder.name,'name':data.get('title') or folder.name,'metadata':data,'inventory':inv,'order':order,'tvdb_id':series_id,'has_nfo':bool(data),'missing_fields':missing,'status':'none' if not data else 'partial' if missing else 'complete','completeness':completeness(inv,remote['episodes']) if remote else None,'checked':remote.get('updated') if remote else None,'saved_assets':state}
    def refresh_index(name):
        init();source,_=folder_pair(name);record=show_record(source,True)
        with db() as connection:connection.execute('INSERT OR REPLACE INTO tv_library VALUES (?,?)',(str(source),json.dumps(record)))
        return record
    def scan(progress=None,cancelled=None):
        init();root,_=roots();progress=progress or (lambda **kw:None);cancelled=cancelled or (lambda:False)
        folders=sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),key=lambda p:p.name.casefold());seen=set();warnings=[];found=0
        progress(total=len(folders),checked=0,found=0,new=0,errors=0)
        with db() as connection:known={row[0] for row in connection.execute('SELECT path FROM tv_library')}
        new=0
        for number,folder in enumerate(folders,1):
            if cancelled():break
            progress(current=folder.name)
            try:
                record=show_record(folder,True,cancelled)
                if cancelled():break
                if record['inventory']['episodes'] or record['inventory']['unrecognized'] or record['has_nfo']:
                    with db() as connection:connection.execute('INSERT OR REPLACE INTO tv_library VALUES (?,?)',(str(folder),json.dumps(record)))
                    seen.add(str(folder));found+=1;new+=str(folder) not in known
            except OSError:
                seen.add(str(folder));warnings.append({'folder':folder.name,'error':'Unable to read this show folder.'})
            progress(checked=number,found=found,new=new,errors=len(warnings),warnings=warnings[-20:])
        if not cancelled():
            with db() as connection:
                for old in known-seen:
                    if Path(old).parent==root:connection.execute('DELETE FROM tv_library WHERE path=?',(old,))
        return {'count':found,'warnings':warnings,'cancelled':cancelled()}
    @app.get('/api/tv/saved-searches')
    def tv_saved_searches():
        init()
        with db() as connection:rows=connection.execute('SELECT id,name,filters,archived FROM tv_saved_searches ORDER BY name COLLATE NOCASE').fetchall()
        return {'searches':[{'id':a,'name':b,'filters':json.loads(c),'archived':bool(d)} for a,b,c,d in rows]}
    @app.post('/api/tv/saved-searches')
    def tv_save_search(req:TVSavedSearch,request:Request):
        m.require_unlocked(request);init()
        if set(req.filters)-{'title','year','certificate','actors','genres','status','missing','unmatched','resolution','codec','audio','subtitles','missing_field'} or any(len(v)>200 for v in req.filters.values()):raise HTTPException(400,'Unsupported TV search filters.')
        if not req.name.strip():raise HTTPException(400,'Enter a saved search name.')
        identifier=req.id or uuid.uuid4().hex
        with db() as connection:
            if req.id and not connection.execute('SELECT 1 FROM tv_saved_searches WHERE id=?',(req.id,)).fetchone():raise HTTPException(404,'TV saved search not found.')
            connection.execute('INSERT OR REPLACE INTO tv_saved_searches VALUES (?,?,?,0)',(identifier,req.name.strip(),json.dumps(req.filters)))
        return {'id':identifier}
    @app.delete('/api/tv/saved-searches/{identifier}')
    def tv_archive_search(identifier:str,request:Request):
        m.require_unlocked(request);init()
        with db() as connection:connection.execute('UPDATE tv_saved_searches SET archived=1 WHERE id=?',(identifier,))
        return {'archived':True}
    @app.get('/api/tv/library')
    def tv_library():
        init();cfg=m.load_config();root=Path(cfg.get('tv_root_path') or '/__artflix_unconfigured_tv__').resolve()
        with db() as connection:rows=connection.execute('SELECT path,data FROM tv_library').fetchall()
        shows=[json.loads(data) for path,data in rows if Path(path).parent==root]
        return {'shows':shows,'root':str(root),'configured':bool(cfg.get('tv_root_path') and cfg.get('tv_write_path'))}
    @app.post('/api/tv/scan/start')
    def tv_scan_start():roots();return jobs.start(scan)
    @app.get('/api/tv/scan/status')
    def tv_scan_status():return jobs.status()
    @app.post('/api/tv/scan/cancel')
    def tv_scan_cancel():return jobs.cancel()
    @app.post('/api/tv/inspect')
    def tv_inspect(req:TVFolder):
        init()
        source,_=folder_pair(req.folder);record=show_record(source,True)
        remote=bundle(record['tvdb_id'],record['order']) if record['tvdb_id'] else None
        episodes={(e.get('seasonNumber'),e.get('number')):e for e in (remote or {}).get('episodes',[])}
        for file in record['inventory']['episodes']:
            episode=episodes.get(tuple(file['numbers'][0]),{})
            if len(file['numbers'])==1:file['provider_credits']=episode.get('credits',{})
            file['provider_thumbnail']=tvdb_image_url(episode.get('image'))
            file['thumbnail']=None
            video=Path(file['file'])
            for suffix in ('.jpg','.jpeg','.png','.webp'):
                name=(video.parent/(video.stem+'-thumb'+suffix)).as_posix()
                try:
                    image=safe_metadata_file(source,name)
                    if image.is_file():
                        file['thumbnail']={'name':name,'url':'/api/tv/image?'+urlencode({'folder':req.folder,'name':name,'v':image.stat().st_mtime_ns})}
                        break
                except HTTPException:continue
        record['multi_episode_filename_format']=m.load_config().get('tv_multi_episode_filename_format','{show} - S{season}E{episode} - {title}')
        record['filename_format']=m.load_config().get('tv_filename_format','{show} - S{season}E{episode} - {title}')
        record['folder_path']=str(source)
        with db() as connection:display=connection.execute('SELECT name FROM tv_display_images WHERE path=?',(str(source),)).fetchone()
        record['display_image']=display[0] if display else ''
        record['artwork']=[]
        for name in ('poster.jpg','poster.png','fanart.jpg','fanart.png','landscape.jpg','banner.jpg','clearlogo.png','clearart.png'):
            if (source/name).is_file() and not (source/name).is_symlink():record['artwork'].append({'name':name,'type':{'poster':'Posters','fanart':'Backgrounds','landscape':'Landscapes','banner':'Banners','clearlogo':'Logos','clearart':'Clearart'}.get(name.split('.')[0],name.split('.')[0]),'url':'/api/tv/image?'+urlencode({'folder':req.folder,'name':name}),'local':True})
        for season,kinds in season_artwork_layout(source,m.VIDEO_EXTENSIONS).items():
            for kind,outputs in kinds.items():
                for name in outputs:
                    file=source/name
                    if file.is_file() and not file.is_symlink() and source in file.resolve().parents:
                        record['artwork'].append({'name':name,'type':('Specials' if season==0 else 'Season '+str(season))+' '+kind.title()+'s','url':'/api/tv/image?'+urlencode({'folder':req.folder,'name':name}),'local':True,'season':season})
        for actor in record['metadata'].get('actors',[]):
            image=actor.get('image','');p=Path(image)
            actor['local_image']='/api/tv/image?'+urlencode({'folder':req.folder,'name':image}) if not p.is_absolute() and not '..' in p.parts and (source/p).is_file() and not (source/p).is_symlink() else ''
        return record
    def safe_metadata_file(source,name):
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or p.suffix.lower() not in {'.nfo','.jpg','.jpeg','.png','.webp'}:raise HTTPException(400,'Only TV metadata and images are permitted.')
        path=source/p
        if source not in path.resolve().parents or any(part.is_symlink() for part in [path,*list(path.parents)[:len(p.parts)-1]]):raise HTTPException(400,'Unsafe TV metadata path.')
        return path
    @app.get('/api/tv/image')
    def tv_image(folder:str,name:str):
        source,_=folder_pair(folder);file=safe_metadata_file(source,name)
        if file.suffix.lower()=='.nfo' or not file.is_file():raise HTTPException(404,'Image unavailable.')
        return FileResponse(file)
    @app.post('/api/tv/search')
    async def tv_search(req:TVSearch):
        query=req.query.strip();year=req.year
        match=re.search(r'\s*[\[(]((?:18|19|20|21)\d{2})[\])]\s*$',query)
        if match and query[:match.start()].strip():
            year=year or int(match[1]);query=query[:match.start()].strip()
        if not query:raise HTTPException(400,'Enter a show name.')
        params={'query':query,'type':'series','limit':15}
        if year:params['year']=year
        result=await provider.get('/search',params)
        return {'results':result.get('data') or [],'query':query,'year':year}
    @app.post('/api/tv/match')
    async def tv_match(req:TVMatch):
        source,_=folder_pair(req.folder);order_valid(req.order)
        data=await provider.bundle(req.tvdb_id,req.order,tv_language(m.load_config()))
        local=inventory(source,m.VIDEO_EXTENSIONS)
        owned={tuple(n) for file in local['episodes'] for n in file['numbers']}
        records=[data['details']];failures=0;loaded=0
        for episode in data['episodes']:
            if (episode.get('seasonNumber'),episode.get('number')) not in owned:continue
            identifier=episode.get('id')
            if not isinstance(identifier,int) or identifier<1:continue
            key=f'tv-episode-credits:{identifier}'
            saved=cached(key)
            usable=saved and saved.get('series_id')==req.tvdb_id
            fresh=usable and time.time()-saved.get('checked',0)<7*86400 and saved.get('last_updated')==episode.get('lastUpdated')
            detail=saved.get('record') if usable else None
            if not fresh:
                try:
                    received=(await provider.get(f'/episodes/{identifier}/extended')).get('data') or {}
                    if received.get('id')!=identifier or received.get('seriesId')!=req.tvdb_id:raise HTTPException(502,'TVDB episode identity did not match.')
                    detail=received
                    m.cache_provider(key,{'series_id':req.tvdb_id,'checked':time.time(),'last_updated':episode.get('lastUpdated'),'record':detail})
                except HTTPException:failures+=1
            if detail:
                records.append(detail);episode['characters']=detail.get('characters') or [];loaded+=1
                if not episode.get('image') and detail.get('image'):episode['image']=detail['image']
        # Resolve numeric roles only when TVDB omits a human-readable peopleType.
        types={};needs_types=any(c.get('type') is not None and not c.get('peopleType') for record in records for c in record.get('characters') or [])
        if needs_types:
            types=cached('tv-people-types') or {}
            if not types:
                try:
                    rows=(await provider.get('/people/types')).get('data') or []
                    types={str(row['id']):row.get('name','') for row in rows if 'id' in row}
                    if types:m.cache_provider('tv-people-types',types)
                except HTTPException:failures+=1
        normalize_credits(data['details'],types)
        for episode in data['episodes']:
            if 'characters' in episode:normalize_credits(episode,types)
        data['credit_status']={'loaded':loaded,'unavailable':failures}
        normalize_episode_images(data)
        m.cache_provider(bundle_key(req.tvdb_id,req.order),data)
        existing=values(read_nfo(source/'tvshow.nfo','tvshow'))
        if str(req.tvdb_id)==existing.get('tvdb_id'):
            refresh_index(req.folder)
        return {'bundle':data,'completeness':completeness(inventory(source,m.VIDEO_EXTENSIONS),data['episodes'])}
    async def fanart_artwork(series_id:int,folder:str|None=None):
        cfg=m.load_config()
        if series_id<1:raise HTTPException(400,'Choose a TVDB series first.')
        if not cfg.get('fanart_key'):return {'artwork':[],'notice':'Set a fanart.tv API key in Settings.'}
        params={'api_key':cfg['fanart_key']}
        if cfg.get('fanart_client_key'):params['client_key']=cfg['fanart_client_key']
        try:
            async with httpx.AsyncClient(timeout=30) as client:response=await client.get('https://webservice.fanart.tv/v3/tv/'+str(series_id),params=params)
            if response.is_error:return {'artwork':[],'notice':f'fanart.tv returned {response.status_code}.'}
            data=response.json();art=[]
            for category,name in [('tvposter','poster.jpg'),('showbackground','fanart.jpg'),('tvthumb','landscape.jpg'),('hdtvlogo','clearlogo.png'),('hdclearart','clearart.png')]:
                art.extend({'name':name,'type':category,'url':item['url'],'lang':item.get('lang','')} for item in data.get(category,[]) if item.get('url') and safe_image(item['url']))
            if folder:
                source,_=folder_pair(folder);layout=season_artwork_layout(source,m.VIDEO_EXTENSIONS)
                for category,kind in [('seasonposter','poster'),('seasonbanner','banner'),('seasonthumb','landscape')]:
                    for item in data.get(category,[]):
                        number=str(item.get('season',''))
                        if not number.isdigit() or int(number) not in layout or not safe_image(item.get('url','')):continue
                        season=int(number);outputs=layout[season][kind]
                        art.append({'name':outputs[0],'outputs':outputs,'type':('Specials' if season==0 else 'Season '+str(season))+' '+kind.title()+'s','url':item['url'],'lang':item.get('lang',''),'season':season})
            return {'artwork':art,'notice':''}
        except (httpx.HTTPError,ValueError):raise HTTPException(502,'Unable to load fanart.tv artwork. Try again later.')
    @app.get('/api/tv/artwork/{series_id}')
    async def tv_artwork(series_id:int,folder:str|None=None):
        if series_id<1:raise HTTPException(400,'Choose a TVDB series first.')
        if folder:folder_pair(folder)
        cfg=m.load_config();art=[];notices=[]
        try:
            result=await fanart_artwork(series_id,folder)
            categories={'tvposter':'Posters','showbackground':'Backgrounds','tvthumb':'Landscapes','hdtvlogo':'Logos','hdclearart':'Clearart'}
            art.extend({**item,'provider':'fanart.tv','type':categories.get(item['type'],item['type'])} for item in result['artwork'])
            if result.get('notice'):notices.append(result['notice'])
        except HTTPException:notices.append('fanart.tv artwork unavailable; other providers are still shown.')
        if cfg.get('tvdb_key'):
            try:
                details=(await provider.get(f'/series/{series_id}/extended')).get('data') or {}
                if details.get('id')!=series_id:raise ValueError('Series identity mismatch')
                types=cached('tv-artwork-types')
                if not types:
                    rows=(await provider.get('/artwork/types')).get('data') or []
                    types={str(row['id']):row.get('name','') for row in rows if 'id' in row}
                    m.cache_provider('tv-artwork-types',types)
                art.extend(tvdb_choices(details,types))
            except (HTTPException,ValueError,httpx.HTTPError):notices.append('TVDB artwork unavailable; other providers are still shown.')
        else:notices.append('Set a TVDB API key to browse TVDB artwork.')
        if cfg.get('tmdb_token'):
            try:
                found=await m.tmdb_get(f'/find/{series_id}',{'external_source':'tvdb_id'})
                matches=found.get('tv_results') or []
                if len(matches)==1 and isinstance(matches[0].get('id'),int):
                    images=await m.tmdb_get(f"/tv/{matches[0]['id']}/images",{'language':''})
                    art.extend(tmdb_choices(images))
                else:notices.append('No unique TMDB match for this TVDB ID; TMDB images were not guessed.')
            except (HTTPException,ValueError,httpx.HTTPError):notices.append('TMDB artwork unavailable; other providers are still shown.')
        else:notices.append('Set a TMDB token to browse TMDB artwork.')
        seen=set();unique=[]
        for item in art:
            key=(item['name'],item['url'])
            if key not in seen:seen.add(key);unique.append(item)
        return {'artwork':unique,'notice':' '.join(notices)}

    @app.post('/api/tv/display-image')
    def set_display_image(req:TVDisplayImage,request:Request):
        m.require_unlocked(request)
        source,_=folder_pair(req.folder)
        if m.library_read_only():raise HTTPException(403,'Library is read-only.')
        record=tv_inspect(TVFolder(folder=req.folder))
        if req.name and req.name not in {item['name'] for item in record['artwork']}:raise HTTPException(400,'Choose a saved show image.')
        with COMMIT_LOCK,db() as connection:connection.execute('INSERT OR REPLACE INTO tv_display_images VALUES (?,?)',(str(source),req.name))
        return {'name':req.name}

    def backup(target,file):
        init();identifier=uuid.uuid4().hex;directory=m.DATA_DIR/'tv-backups'/identifier;directory.mkdir(parents=True)
        shutil.copy2(target/file,directory/'content')
        with db() as connection:connection.execute('INSERT INTO tv_backups VALUES (?,?,?,?)',(identifier,str(target),file,time.time()))
    def validate_pattern(pattern):
        remainder=re.sub(r'\{(?:show|season|episode|title)\}', '', pattern)
        if not pattern.strip() or len(pattern)>180 or any(c in pattern for c in '/\\') or '{' in remainder or '}' in remainder:
            raise HTTPException(400,'TV filename format supports {show}, {season}, {episode}, {title}.')
    def episode_destination(file,records,pattern,show_title):
        validate_pattern(pattern)
        if not all(records):raise HTTPException(409,'This episode is absent from the selected TVDB order. Refresh the show or check its match.')
        numbers=file['numbers'];p=Path(file['file'])
        tokens={'show':show_title,'season':f'{numbers[0][0]:02d}','episode':'E'.join(f'{n[1]:02d}' for n in numbers),'title':' + '.join(e.get('name') or '' for e in records)}
        # Substitute once so braces occurring in a show or episode title stay literal.
        name=re.sub(r'\{(show|season|episode|title)\}',lambda match:tokens[match[1]],pattern)
        return p.with_name(m.safe_filename(name)+p.suffix)
    @app.get('/api/tv/naming-templates')
    def naming_templates():
        cfg=m.load_config()
        return {'default':cfg.get('tv_filename_format','{show} - S{season}E{episode} - {title}'),'templates':(cfg.get('tv_naming_templates') or {'Show and episode title':'{show} - S{season}E{episode} - {title}','Season and episode':'S{season}E{episode} - {title}'})}
    @app.post('/api/tv/naming-templates')
    def save_naming_template(req:TVNamingTemplate,request:Request):
        m.require_unlocked(request);validate_pattern(req.pattern)
        if not req.name.strip():raise HTTPException(400,'Enter a template name.')
        with COMMIT_LOCK:
            cfg=m.load_config();items=dict(naming_templates()['templates'])
            if len(items)>=50 and req.name.strip() not in items:raise HTTPException(400,'A maximum of 50 naming templates is supported.')
            items[req.name.strip()]=req.pattern;cfg['tv_naming_templates']=items
            if req.make_default:cfg['tv_filename_format']=req.pattern
            m.save_config(cfg)
        return naming_templates()
    @app.post('/api/tv/rename-example')
    def tv_rename_example(req:TVRenameExample):
        source,target=folder_pair(req.folder);order_valid(req.order);validate_pattern(req.filename_format)
        file=next((f for f in inventory(source,m.VIDEO_EXTENSIONS)['episodes'] if f['file']==req.video),None)
        if not file:raise HTTPException(400,'Choose a numbered episode file in this show.')
        remote=bundle(req.tvdb_id,req.order) if req.tvdb_id else None
        if not remote:raise HTTPException(409,'Refresh this show from TVDB to load episode titles for the rename example.')
        mapping={(e.get('seasonNumber'),e.get('number')):e for e in remote['episodes']}
        records=[mapping.get(tuple(n)) for n in file['numbers']]
        pattern=(req.multi_episode_filename_format or m.load_config().get('tv_multi_episode_filename_format',req.filename_format)) if len(file['numbers'])>1 else req.filename_format
        destination=episode_destination(file,records,pattern,req.title or source.name)
        before=file['file'];after=destination.as_posix()
        conflict=after!=before and os.path.lexists(target/destination)
        return {'before':before,'after':after,'conflict':conflict,'unchanged':before==after}
    def update_thumb_references(doc,original,renames):
        if doc is None:return False
        changed=False
        for node in doc.findall('thumb'):
            value=(node.text or '').strip()
            if not value or '://' in value:continue
            old=(original.parent/value).as_posix()
            if old in renames:
                node.text=Path(renames[old]).name;changed=True
        return changed
    def plan(req):
        source,target=folder_pair(req.folder);order_valid(req.order)
        if (target/'.artflix-tv.json').is_symlink():raise HTTPException(400,'Unsafe TV asset state path.')
        remote=bundle(req.tvdb_id,req.order) if req.tvdb_id else None
        old=values(read_nfo(source/'tvshow.nfo','tvshow'))
        if req.tvdb_id and (not remote) and str(req.tvdb_id)!=old.get('tvdb_id'):raise HTTPException(409,'Review this TVDB match before saving.')
        allowed={'title','original_title','year','premiered','runtime','plot','certificate','status','tagline','genres','studios','directors','writers'}
        if set(req.metadata)-allowed:raise HTTPException(400,'Unsupported TV metadata field.')
        metadata={**{k:v for k,v in old.items() if isinstance(v,str)},**req.metadata,'tvdb_id':str(req.tvdb_id) if req.tvdb_id else ''}
        details=(remote or {}).get('details') or {};actors=old.get('actors') or []
        for field in ('directors','writers'):
            if field not in req.metadata and not metadata.get(field) and details.get('credits',{}).get(field):metadata[field]=details['credits'][field]
        if remote:
            actors=[{'id':str(c.get('peopleId') or c.get('id') or ''),'name':c.get('personName') or c.get('name') or '', 'role':c.get('name') if c.get('personName') else '', 'image':c.get('personImgURL') or c.get('image') or ''} for c in tv_cast(details) if c.get('personName') or c.get('name')]
        remote_images={str(a['id']):a.get('image','') for a in actors}
        old_actors={str(a.get('id')):a for a in old.get('actors',[])}
        for actor in actors:
            old_image=old_actors.get(str(actor['id']),{}).get('image','')
            if old_image and not old_image.startswith('https://'):
                try:
                    if safe_metadata_file(target,old_image).is_file():actor['image']=old_image
                except HTTPException:pass
        portraits={};actor_map={str(a['id']):a for a in actors}
        for identifier in req.actor_ids:
            actor=actor_map.get(identifier)
            if not actor or not safe_image(remote_images.get(identifier,'')):raise HTTPException(400,'Unsupported actor portrait selection.')
            name='actors/'+m.safe_filename(actor['name'])+'--'+m.safe_filename(identifier)+'.jpg';portraits[name]=remote_images[identifier];actor['image']=name
        writes={'tvshow.nfo':serialize_nfo('tvshow',metadata,read_nfo(source/'tvshow.nfo','tvshow'),actors)}
        if req.artwork_only:
            if req.metadata or req.actor_ids or req.rename_episodes or req.folder_name or req.episode_nfos or req.episode_metadata:raise HTTPException(400,'Artwork-only saves cannot contain metadata or rename changes.')
            writes={}
        images={**portraits}
        season_outputs={name for kinds in season_artwork_layout(source,m.VIDEO_EXTENSIONS).values() for names in kinds.values() for name in names}
        for name,url in req.artwork.items():
            if name not in {'poster.jpg','fanart.jpg','landscape.jpg','banner.jpg','clearlogo.png','clearart.png'}|season_outputs or not safe_image(url):raise HTTPException(400,'Unsupported TV artwork selection.')
            images[name]=url
        inv=inventory(source,m.VIDEO_EXTENSIONS);episode_map={(e.get('seasonNumber'),e.get('number')):e for e in (remote or {}).get('episodes',[])}
        renames={};episode_diffs=[];thumbnail_sources={}
        thumbnail_summary={'requested':0,'ready':0,'skipped_existing':0,'unavailable':0,'unmatched':0}
        candidates={e['file'] for e in inv['episodes']}
        if len(req.thumbnail_files)!=len(set(req.thumbnail_files)) or set(req.thumbnail_files)-candidates:raise HTTPException(400,'Thumbnail selections must refer to numbered files in this show.')
        if (req.episode_thumbnails or req.thumbnail_files) and not remote:raise HTTPException(409,'Refresh this show from TVDB to load episode thumbnails.')
        if set(req.episode_metadata)-candidates:raise HTTPException(400,'Episode edits must refer to numbered files in this show.')
        if any(set(fields)-{'title','plot','premiered','runtime','certificate','writers','directors'} for fields in req.episode_metadata.values()):raise HTTPException(400,'Unsupported episode metadata field.')
        if req.episode_nfos and not remote:raise HTTPException(409,'Fetch the TVDB episode list before generating episode NFOs.')
        if req.rename_episodes and not remote:raise HTTPException(409,'Fetch the TVDB episode list before renaming episodes.')
        pattern=req.filename_format
        validate_pattern(pattern)
        for file in inv['episodes']:
            p=Path(file['file']);numbers=[tuple(n) for n in file['numbers']];records=[episode_map.get(n) for n in numbers]
            destination=p
            if req.rename_episodes:
                if not all(records):raise HTTPException(409,'Some numbered episodes are absent from the selected TVDB order; rename was not approved.')
                destination=episode_destination(file,records,(req.multi_episode_filename_format or m.load_config().get('tv_multi_episode_filename_format',pattern)) if len(numbers)>1 else pattern,metadata.get('title') or source.name)
                if destination!=p:
                    renames[p.as_posix()]=destination.as_posix()
                    parent=source/p.parent
                    excluded=[Path(e).stem for e in inv['extras'] if Path(e).parent==p.parent]
                    for side in parent.iterdir():
                        if side.is_symlink() or not side.is_file():continue
                        if side.stem==p.stem+'-thumb' and side.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}:
                            renames[side.relative_to(source).as_posix()]=(destination.parent/(destination.stem+'-thumb'+side.suffix)).as_posix()
                            continue
                        if side.suffix.lower() not in {'.srt','.ass','.ssa','.sub','.idx','.vtt','.sup','.nfo'}:continue
                        if any(side.stem==stem or side.name.startswith(stem+'.') for stem in excluded):continue
                        if side.stem==p.stem or side.name.startswith(p.stem+'.'):
                            renames[side.relative_to(source).as_posix()]=(destination.parent/(destination.stem+side.name[len(p.stem):])).as_posix()
            thumb_name=None
            if req.episode_thumbnails or file['file'] in req.thumbnail_files:
                # A combined file uses its first numbered episode's still.
                thumbnail_summary['requested']+=1
                url=tvdb_image_url((records[0] or {}).get('image'))
                if safe_image(url):
                    from urllib.parse import urlparse
                    suffix=Path(urlparse(url).path).suffix.lower()
                    suffix=suffix if suffix in {'.jpg','.jpeg','.png','.webp'} else '.jpg'
                    thumb_name=(destination.parent/(destination.stem+'-thumb'+suffix)).as_posix()
                    existing=any((target/p.parent/(p.stem+'-thumb'+ext)).exists() for ext in ('.jpg','.jpeg','.png','.webp'))
                    if existing and not (req.replace_existing or m.load_config().get('overwrite',False)):
                        if file['file'] in req.thumbnail_files:raise HTTPException(409,'An episode thumbnail already exists. Enable replacement and review again.')
                        thumbnail_summary['skipped_existing']+=1
                        thumb_name=None  # Bulk downloads fill missing thumbnails by default.
                    if thumb_name:
                        images[thumb_name]=url;thumbnail_sources[thumb_name]=url;thumbnail_summary['ready']+=1
                else:
                    thumbnail_summary['unavailable' if records[0] else 'unmatched']+=1
                    if file['file'] in req.thumbnail_files:raise HTTPException(409,'TVDB has no supported thumbnail for this episode. Refresh the show to check again.')
            if req.episode_nfos or file['file'] in req.episode_metadata:
                if len(numbers)!=1:raise HTTPException(409,'Combined-episode videos retain their existing NFO. Disable episode NFO generation for this save; multi-episode renaming is supported.')
                ep=records[0] or {}
                if not ep and file['file'] not in req.episode_metadata:continue
                name=destination.with_suffix('.nfo').as_posix()
                episode_doc=read_nfo(source/p.with_suffix('.nfo'),'episodedetails')
                update_thumb_references(episode_doc,p,renames)
                episode_values={k:v for k,v in values(episode_doc).items() if isinstance(v,str)}
                if ep:episode_values.update(title=ep.get('name') or '',plot=ep.get('overview') or '',premiered=ep.get('aired') or '',runtime=str(ep.get('runtime') or ''),tvdb_id=str(ep.get('id') or ''))
                for field in ('directors','writers'):
                    if not episode_values.get(field) and ep.get('credits',{}).get(field):episode_values[field]=ep['credits'][field]
                episode_values.update(req.episode_metadata.get(file['file'],{}))
                if thumb_name:episode_values['thumb']=Path(thumb_name).name
                content=serialize_nfo('episodedetails',episode_values,episode_doc,episode={'season':numbers[0][0],'episode':numbers[0][1]})
                before=values(episode_doc)
                after=values(ET.fromstring(content))
                episode_diffs.extend({'field':name+' / '+key,'before':before.get(key,''),'after':value} for key,value in after.items() if isinstance(value,str) and value!=before.get(key,''))
                writes[name]=content
            elif req.rename_episodes:
                episode_doc=read_nfo(source/p.with_suffix('.nfo'),'episodedetails')
                if update_thumb_references(episode_doc,p,renames):
                    writes[destination.with_suffix('.nfo').as_posix()]=ET.tostring(episode_doc,encoding='utf-8',xml_declaration=True)
        folder_name=req.folder_name.strip() if req.folder_name else target.name
        if folder_name!=m.safe_filename(folder_name) or folder_name in {'.','..'}:raise HTTPException(400,'Invalid TV folder name.')
        if folder_name!=target.name and (target.parent/folder_name).exists():raise HTTPException(409,'TV folder rename destination already exists.')
        if len(set(renames.values()))!=len(renames):raise HTTPException(409,'Episode rename destinations collide.')
        for before,after in renames.items():
            if (target/after).exists() or (target/after).is_symlink():raise HTTPException(409,'Episode rename destination already exists.')
        replace=req.replace_existing or m.load_config().get('overwrite',False)
        for name in [*writes,*images]:
            file=safe_metadata_file(target,name)
            # A moved NFO is considered existing at its new path too.
            exists=file.exists() or name in renames.values()
            if exists and not replace:raise HTTPException(409,'An output exists. Enable replacement to review overwriting it: '+name)
        signature=fingerprint({'kind':'tv-write','request':req.model_dump(exclude={'review_token','selected_operations'}),'root':str(source),'target':str(target),'source':tree(source),'files':tree(target),'remote':remote,'settings':m.load_config()})
        return {'source':source,'target':target,'metadata':metadata,'old':old,'writes':writes,'images':images,'renames':renames,'folder_name':folder_name,'signature':signature,'episode_diffs':episode_diffs,'thumbnail_sources':thumbnail_sources,'thumbnail_summary':thumbnail_summary}
    def public_plan(p):
        _,_,_,unrename=subset_outputs({}, {}, p['renames'],set())
        files=[]
        for name in [*p['writes'],*p['images']]:
            old=next((old for old,new in p['renames'].items() if new==name),name)
            exists=(p['target']/name).exists() or name in p['renames'].values()
            files.append({'path':name,'action':'Replace' if exists else 'Create','without_rename':unrename(name),'without_rename_action':'Replace' if (p['target']/unrename(name)).exists() else 'Create','before_url':('/api/tv/image?'+urlencode({'folder':p['source'].name,'name':old})) if name in p['images'] and exists else None,'after_url':p['images'].get(name)})
        return {'selection_supported':True,'thumbnails':p['thumbnail_summary'],'folder_before':p['target'].name,'folder_after':p['folder_name'],'metadata':[{'field':k,'before':p['old'].get(k,''),'after':v} for k,v in p['metadata'].items() if p['old'].get(k,'')!=v and 'tvshow.nfo' in p['writes']]+p['episode_diffs'],'files':files,'renames':[{'before':a,'after':b} for a,b in p['renames'].items()],'review_token':m.review_signer.sign(p['signature'])}
    @app.post('/api/tv/write/preview')
    def tv_preview(req:TVWrite):return public_plan(plan(req))
    @app.post('/api/tv/write')
    async def tv_write(req:TVWrite,request:Request):
        m.require_library_writes(request);p=plan(req)
        if not m.review_signer.valid(req.review_token or "",p['signature']):raise HTTPException(409,'TV review expired or files changed. Review again.')
        selected=select_operations(req.selected_operations,[*p['writes'],*p['images']],p['renames'],p['target'].name,p['folder_name'])
        payload,chosen_images,moves,remap=subset_outputs(p['writes'],p['images'],p['renames'],selected)
        for name in [*payload,*chosen_images]:
            file=safe_metadata_file(p['target'],name)
            if file.exists() and not (req.replace_existing or m.load_config().get('overwrite',False)):raise HTTPException(409,'Existing file protected after excluding renames. Enable replacement and review again: '+name)
        try:
            async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
                for name,url in chosen_images.items():
                    payload[name]=await m.download_image(client,url)
        except httpx.HTTPError:raise HTTPException(502,'Image download failed. No TV files were changed.')
        with COMMIT_LOCK:
            m.require_library_writes(request);current=plan(req)
            if current['signature']!=p['signature']:raise HTTPException(409,'TV files changed during download. Review again.')
            p['renames']=moves
            if 'rename:folder' not in selected:p['folder_name']=p['target'].name
            p['thumbnail_sources']={remap(name):url for name,url in p['thumbnail_sources'].items() if 'file:'+name in selected}
            selected_art={remap(name):url for name,url in req.artwork.items() if 'file:'+name in selected}
            selected_actors=[identifier for identifier in req.actor_ids if any(name.startswith('actors/') and ('--'+m.safe_filename(identifier)+'.') in name for name in chosen_images)]
            target=p['target'];moved=[];created=[];old_contents={};folder_after=target
            try:
                # Back up all replaced metadata and connected NFOs before mutation.
                for name in set(payload)|{n for n in p['renames'] if Path(n).suffix.lower() in {'.nfo','.jpg','.jpeg','.png','.webp'}}:
                    if (target/name).is_file():backup(target,name);old_contents[name]=(target/name).read_bytes()
                grouped={}
                for before,after in p['renames'].items():grouped.setdefault(str(Path(before).parent),{})[Path(before).name]=Path(after).name
                for parent,mapping in grouped.items():
                    rename_media_files(target/parent,mapping);moved.extend((str(Path(parent)/a),str(Path(parent)/b)) for a,b in mapping.items())
                for name,data in payload.items():
                    dest=safe_metadata_file(target,name);dest.parent.mkdir(parents=True,exist_ok=True)
                    if not dest.exists():created.append(name)
                    fd,temp=tempfile.mkstemp(dir=dest.parent,prefix='.artflix-')
                    try:
                        with os.fdopen(fd,'wb') as handle:handle.write(data);handle.flush();os.fsync(handle.fileno())
                        if dest.exists():os.replace(temp,dest)
                        else:rename_no_replace(Path(temp),dest)
                    finally:
                        if os.path.exists(temp):os.unlink(temp)
                previous=show_record(p['source']).get('saved_assets',{})
                state={'order':req.order,'artwork':{**{p['renames'].get(name,name):url for name,url in previous.get('artwork',{}).items()},**selected_art,**p['thumbnail_sources']},'actor_ids':list(set(previous.get('actor_ids',[]))|set(selected_actors))}
                # This app-owned state is not media and contains no credentials.
                statefile=target/'.artflix-tv.json'
                if statefile.is_symlink():raise HTTPException(409,'TV state file is a symlink.')
                fd,state_temp=tempfile.mkstemp(dir=target,prefix='.artflix-state-')
                try:
                    with os.fdopen(fd,'w') as handle:json.dump(state,handle);handle.flush();os.fsync(handle.fileno())
                    if statefile.exists():os.replace(state_temp,statefile)
                    else:rename_no_replace(Path(state_temp),statefile)
                finally:
                    if os.path.exists(state_temp):os.unlink(state_temp)
                if p['folder_name']!=target.name:folder_after=target.with_name(p['folder_name']);rename_no_replace(target,folder_after)
            except Exception:
                # Videos are only moved; roll moves back without overwriting anything.
                for name in created:
                    file=target/name
                    if file.is_file() and not file.is_symlink():file.unlink()
                for a,b in reversed(moved):
                    if (target/b).exists() and not (target/a).exists():rename_no_replace(target/b,target/a)
                for name,content in old_contents.items():
                    file=target/name
                    if file.parent.is_dir() and not file.is_symlink():file.write_bytes(content)
                raise
            if folder_after!=target:
                init()
                with db() as connection:
                    connection.execute('UPDATE tv_backups SET folder=? WHERE folder=?',(str(folder_after),str(target)))
                    connection.execute('UPDATE tv_display_images SET path=? WHERE path=?',(str(p['source'].with_name(folder_after.name)),str(p['source'])))
                    connection.execute('DELETE FROM tv_library WHERE path=?',(str(p['source']),))
            refresh_index(folder_after.name)
        return {'folder':folder_after.name,'written':list(payload),'videos_preserved':True,'thumbnails':{**p['thumbnail_summary'],'downloaded':len(p['thumbnail_sources'])}}
    def reset_plan(name):
        source,target=folder_pair(name);inv=inventory(source,m.VIDEO_EXTENSIONS)
        candidates={'tvshow.nfo'}|{str(Path(e['file']).with_suffix('.nfo')) for e in inv['episodes']}
        candidates|={(Path(e['file']).parent/(Path(e['file']).stem+'-thumb'+suffix)).as_posix() for e in inv['episodes'] for suffix in ('.jpg','.jpeg','.png','.webp')}
        for directory,dirs,files in os.walk(target,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink() and (d in {'actors','.actors'} or d.lower().startswith('season '))]
            for name in files:
                file=Path(directory)/name
                if file.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}:candidates.add(file.relative_to(target).as_posix())
        names=[n for n in sorted(candidates) if (target/n).is_file() and not (target/n).is_symlink()]
        for name in names:safe_metadata_file(target,name)
        signature=fingerprint({'kind':'tv-reset','folder':str(target),'files':tree(target),'settings':m.load_config()})
        return target,names,signature
    @app.post('/api/tv/reset/preview')
    def tv_reset_preview(req:TVReset):
        target,names,signature=reset_plan(req.folder)
        return {'folder_before':req.folder,'folder_after':req.folder,'files':[{'path':n,'action':'Remove metadata (backed up)'} for n in names],'review_token':m.review_signer.sign(signature)}
    @app.post('/api/tv/reset')
    def tv_reset(req:TVReset,request:Request):
        with COMMIT_LOCK:
            m.require_library_writes(request);target,names,signature=reset_plan(req.folder)
            if not m.review_signer.valid(req.review_token or "",signature):raise HTTPException(409,'TV reset review expired or files changed.')
            for name in names:backup(target,name)
            for name in names:(target/name).unlink()
            state=target/'.artflix-tv.json'
            if state.is_file() and not state.is_symlink():state.unlink()
            refresh_index(req.folder)
        return {'removed':names,'videos_preserved':True,'subtitles_preserved':True}
    @app.get('/api/tv/backups')
    def tv_backups(folder:str):
        init();_,target=folder_pair(folder)
        with db() as connection:rows=connection.execute('SELECT id,file,created FROM tv_backups WHERE folder=? ORDER BY created DESC',(str(target),)).fetchall()
        return {'backups':[{'id':a,'file':b,'created':c} for a,b,c in rows]}
    def restore_plan(req):
        init();_,target=folder_pair(req.folder)
        with db() as connection:row=connection.execute('SELECT file FROM tv_backups WHERE id=? AND folder=?',(req.backup_id,str(target))).fetchone()
        if not row or not req.backup_id.isalnum():raise HTTPException(404,'TV backup not found.')
        file=safe_metadata_file(target,row[0]);content=m.DATA_DIR/'tv-backups'/req.backup_id/'content'
        if not content.is_file() or content.is_symlink():raise HTTPException(404,'Backup content missing.')
        signature=fingerprint({'kind':'tv-restore','folder':str(target),'file':row[0],'snapshot':tree(target),'backup':file_signature(content)})
        return target,file,content,signature
    @app.post('/api/tv/backups/preview')
    def tv_restore_preview(req:TVRestore):
        target,file,content,signature=restore_plan(req)
        return {'folder_before':req.folder,'folder_after':req.folder,'files':[{'path':file.relative_to(target).as_posix(),'action':'Restore metadata backup'}],'review_token':m.review_signer.sign(signature)}
    @app.post('/api/tv/backups/restore')
    def tv_restore(req:TVRestore,request:Request):
        with COMMIT_LOCK:
            m.require_library_writes(request);target,file,content,signature=restore_plan(req)
            if not m.review_signer.valid(req.review_token or "",signature):raise HTTPException(409,'TV restore review expired or files changed.')
            if file.exists():backup(target,file.relative_to(target).as_posix())
            file.parent.mkdir(parents=True,exist_ok=True)
            fd,temp=tempfile.mkstemp(dir=file.parent,prefix='.artflix-restore-')
            try:
                with os.fdopen(fd,'wb') as handle:handle.write(content.read_bytes())
                if file.exists():os.replace(temp,file)
                else:rename_no_replace(Path(temp),file)
            finally:
                if os.path.exists(temp):os.unlink(temp)
            refresh_index(req.folder)
        return {'restored':file.name}
    return {'jobs':jobs,'scan':scan,'provider':provider,'roots':roots}
